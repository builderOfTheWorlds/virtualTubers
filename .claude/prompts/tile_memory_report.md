# Memory Growth Analysis: `tile_pane.py` (8 instances/container)

## Executive Summary
The observed growth of ~1.8 GB/day per process (totaling ~4-5 GB over 24h) is **not** caused by unbounded data structures (leaks) in the application logic. All identified data structures (deques, lists, dicts) are either bounded by `maxlen`, replaced on each cycle, or explicitly cleaned up in `finally` blocks.

The growth is almost certainly caused by **Heap Fragmentation** and **glibc `brk` behavior** triggered by high-frequency, large-allocation churn in the rendering and audio pipelines. The Python interpreter and C extensions (NumPy, Pygame, Postgres driver) allocate large blocks (MBs) frequently. When these blocks are freed, glibc often does not return the memory to the OS (`brk` only shrinks the heap if the *top* of the heap is free). Instead, the memory remains in the process's private anonymous heap as "free but unusable" space, leading to a steady increase in RSS (Resident Set Size) even if the actual live object count is stable.

---

## 1. Ranked Likely Causes of Growth

*Note: "Leak" implies objects are never freed. "Fragmentation" implies objects are freed, but the memory allocator (glibc) fails to return the memory to the OS, causing RSS to grow.*

### Rank 1: High-Frequency Large Array Churn (Fragmentation)
**File:** `app/avatar_providers/codec_avatar.py`
**Lines:** 238, 248, 254, 260, 621, 623
**Mechanism:**
Every tick (12 fps), the code allocates and frees multiple full-frame NumPy arrays (RGB, float32, etc.) and Pygame surfaces.
- `build_codec_head` (L238): Allocates new `verts`, `faces`, `materials` arrays.
- `gl_raster.render_with_fallback` (L248): Allocates full-frame RGB array.
- `apply_codec_screen` (L254): Allocates new full-frame array.
- `composite_on_background` (L260): Allocates new full-frame array.
- `np.clip` / `.astype` (L621): Allocates **two** additional full-frame arrays.
- `surfarray.make_surface` (L623): Allocates a new Pygame surface (which wraps a C-level buffer).

**Why it causes growth:**
At 12 fps, this is ~144 large allocations per second per process. With 8 processes, that's ~1,152 large allocations/sec. If the frame size is 1080p (1920x1080x3 bytes ≈ 6MB), each tick allocates ~30-50MB of temporary memory. glibc's `malloc`/`free` cycle for large blocks often uses `mmap`/`munmap` for very large sizes, but for medium-large sizes (or if `M_MMAP_THRESHOLD` is high), it may use `brk`. Even with `mmap`, if the address space is fragmented, the OS may not reclaim the pages immediately, or the Python/NumPy memory pools may hold onto them. More critically, if these allocations are not perfectly aligned or if the allocator cannot coalesce freed blocks with the top of the heap, the `brk` heap grows monotonically.

### Rank 2: GPU Worker Pickling & Shared Memory Views (Fragmentation)
**File:** `app/gpu_render_worker.py`
**Lines:** 106, 208, 197, 108
**Mechanism:**
- L208: `self._frame.astype(np.float32)` creates a new 5.6MB array per tick.
- L197/L108: `pickle` serialization of messages and responses. Pickling large arrays creates temporary byte buffers.

**Why it causes growth:**
Similar to Rank 1, but in the subprocess. However, the main process (`tile_pane.py`) also participates in this via shared memory views and queue communication. The main process may be holding references to shared memory buffers or temporary pickled data that are not immediately released, or the glibc heap in the main process is fragmented by the interaction with the `multiprocessing` queues.

### Rank 3: Postgres Audio Bytea Loading (Fragmentation)
**File:** `app/narration_store.py`
**Lines:** 104, 131, 285
**Mechanism:**
- L104: `narration.audio_path.read_bytes()` loads entire WAV files into memory.
- L131: `bytes(row[5])` copies audio `bytea` from DB into a new `bytes` object.
- L285: `bytes(row[0])` copies single scene audio.

**Why it causes growth:**
WAV files can be large (e.g., 10-50MB for a few seconds of high-quality audio). Loading these into Python `bytes` objects creates large, contiguous allocations. When these objects are freed, the memory may not be returned to the OS if the heap is fragmented. This is a classic source of RSS growth in long-running Python processes that handle large binary blobs.

### Rank 4: Audio Player Subprocess Spawning (Fragmentation/Leak Risk)
**File:** `app/audio_player.py`
**Lines:** 76, 77
**Mechanism:**
- L76: `subprocess.Popen` creates a child process for each WAV playback.
- L77: File descriptors for `stdout`/`stderr` are allocated.

**Why it causes growth:**
While the `Popen` object is released in `finally`, the act of spawning a subprocess involves allocating file descriptors, pipes, and potentially shared memory regions. If the child process is not reaped correctly (e.g., if `wait()` is not called or if the process is a zombie), it can leak file descriptors and memory. However, the report states it is "bounded/harmless," so this is likely not the primary cause, but it contributes to system-level resource churn.

---

## 2. Minimal Fixes

### Fix 1: Reuse NumPy Arrays in `codec_avatar.py` (High Impact)
**Problem:** Allocating new full-frame arrays every tick.
**Fix:** Pre-allocate the largest possible frame arrays once and reuse them.

```python
# In codec_avatar.py, inside the class or module scope:
import numpy as np

# Pre-allocate buffers (adjust size to max expected frame)
_MAX_FRAME_SIZE = (1920, 1080)
_rgb_buffer = np.zeros(_MAX_FRAME_SIZE + (3,), dtype=np.uint8)
_float_buffer = np.zeros(_MAX_FRAME_SIZE + (3,), dtype=np.float32)

def build_codec_head(...):
    # Instead of: verts = np.array(...)
    # Reuse pre-allocated buffers if possible, or use np.empty and fill
    # For verts/faces, if size is constant, pre-allocate once.
    pass

def gl_raster.render_with_fallback(...):
    # Instead of: frame = np.empty((h, w, 3), dtype=np.uint8)
    # Use: frame = _rgb_buffer[:h, :w, :]
    # Ensure no data is retained after use.
    pass

def apply_codec_screen(...):
    # Reuse _rgb_buffer or a dedicated buffer
    pass

def composite_on_background(...):
    # Reuse _rgb_buffer
    pass

def _post_process(frame):
    # Instead of: clipped = np.clip(frame, 0, 255)
    # Use: np.clip(frame, 0, 255, out=frame)  # In-place
    # Instead of: astype_frame = frame.astype(np.float32)
    # Use: np.asarray(frame, dtype=np.float32, out=_float_buffer)  # If sizes match
    pass
```

### Fix 2: Reuse Buffers in `gpu_render_worker.py` (Medium Impact)
**Problem:** `astype` creates a new array every tick.
**Fix:** Pre-allocate the float32 buffer and use `out` parameter.

```python
# In gpu_render_worker.py
class GPURenderWorker:
    def __init__(self, ...):
        # Pre-allocate float32 buffer
        self._float_frame = np.zeros((h, w, 3), dtype=np.float32)
    
    def render(self, ...):
        # Instead of: self._frame.astype(np.float32)
        # Use:
        np.asarray(self._frame, dtype=np.float32, out=self._float_frame)
        # Then use self._float_frame for rendering
        pass
```

### Fix 3: Stream Audio Instead of Loading Entire File (Medium Impact)
**Problem:** `read_bytes()` and `bytes(row[5])` load entire WAV files into memory.
**Fix:** Use a streaming approach or limit the size of audio chunks.

```python
# In narration_store.py
def load_airing(airing_id):
    # Instead of: audio_data = narration.audio_path.read_bytes()
    # If possible, stream the audio to the player without loading into memory.
    # If not, ensure the bytes object is explicitly deleted after use.
    audio_data = narration.audio_path.read_bytes()
    try:
        # Use audio_data
        pass
    finally:
        del audio_data  # Explicitly delete to help GC
```

### Fix 4: Tune glibc Malloc (Low Effort, High Impact)
**Problem:** glibc `brk` heap grows and doesn't shrink.
**Fix:** Set environment variables to encourage `mmap` for large allocations and to trim the heap more aggressively.

```bash
# In the container startup script or environment:
export MALLOC_TRIM_THRESHOLD_=131072  # Trim heap if free space > 128KB
export M_MMAP_THRESHOLD=131072        # Use mmap for allocations > 128KB
export M_MMAP_MAX=0                   # No limit on mmap allocations
```

This forces glibc to use `mmap`/`munmap` for larger allocations, which returns memory to the OS more reliably than `brk`.

---

## 3. One Experiment to Confirm in Production

**Experiment:** Enable `MALLOC_TRIM_THRESHOLD_` and `M_MMAP_THRESHOLD` environment variables and monitor RSS growth.

**Steps:**
1. Deploy a canary instance of the container with the following environment variables set:
   ```bash
   MALLOC_TRIM_THRESHOLD_=131072
   M_MMAP_THRESHOLD=131072
   M_MMAP_MAX=0
   ```
2. Monitor the RSS (Resident Set Size) of the `tile_pane.py` processes over 24 hours using `psutil` or `cgroup` metrics.
3. Compare the growth rate to the baseline (without these variables).

**Expected Outcome:**
- If the growth rate drops significantly (e.g., from 1.8 GB/day to <0.5 GB/day), the cause is **glibc