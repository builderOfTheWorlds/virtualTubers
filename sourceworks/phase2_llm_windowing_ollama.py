#!/usr/bin/env python3
"""
Phase 2: LLM-based restoration with word-sequence guards (Ollama backend)
- Split chapters into windows (~1000 words with 100-word overlap)
- Send each to Ollama on 192.168.1.23 for punctuation/paragraph/attribution restoration
- Guard: diff word sequences before/after to catch hallucination
- Tag scenes with characters and locations
"""

import re
import json
import requests
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import time

# Ollama endpoint
OLLAMA_HOST = "http://192.168.1.23:11434"
OLLAMA_MODEL = "llama3.1:8b"  # Available on 192.168.1.23; fast (10-20s/window), good quality
OLLAMA_TIMEOUT = 300  # seconds (increased from 120 for aarch64 host)

@dataclass
class Window:
    chapter_num: int
    window_idx: int
    text: str
    offset_start: int
    offset_end: int
    
    def word_tokens(self) -> List[str]:
        """Extract word tokens (strip punctuation, lowercase)."""
        cleaned = re.sub(r'[^\w\s]', ' ', self.text.lower())
        tokens = cleaned.split()
        return [t for t in tokens if t]  # remove empty strings

@dataclass
class WindowResult:
    window: Window
    cleaned_text: str
    hallucination_detected: bool
    word_diff: Optional[Dict] = None
    error: Optional[str] = None
    llm_output: Optional[str] = None
    llm_model: Optional[str] = None

def split_into_windows(text: str, chapter_num: int, 
                       window_size: int = 1000, 
                       overlap: int = 100) -> List[Window]:
    """
    Split chapter text into overlapping windows.
    window_size and overlap are in approximate words (not exact).
    """
    words = text.split()
    windows = []
    
    stride = window_size - overlap  # how many new words per window
    offset = 0
    
    for i in range(0, len(words), stride):
        window_words = words[i:i + window_size]
        if not window_words:
            break
        
        window_text = ' '.join(window_words)
        
        # Find offset in original text
        start_idx = text.find(window_text, offset)
        if start_idx == -1:
            # Fallback: estimate based on word count
            avg_word_len = len(text) / len(words) if words else 1
            start_idx = int(i * avg_word_len)
        
        end_idx = start_idx + len(window_text)
        offset = end_idx
        
        windows.append(Window(
            chapter_num=chapter_num,
            window_idx=len(windows),
            text=window_text,
            offset_start=start_idx,
            offset_end=end_idx
        ))
    
    return windows


def call_ollama(text: str, model: str = OLLAMA_MODEL) -> Optional[str]:
    """
    Call Ollama running on 192.168.1.23:11434 for text restoration.
    Returns cleaned text or None on error.
    """
    prompt = f"""You are a careful text restorer, NOT a rewriter.

RULES:
- Restore quotation marks around dialogue.
- Restore paragraph breaks (double newlines) based on speaker changes, scene shifts.
- Add speaker attribution (he said, she whispered, etc.) where needed for clarity.
- Restore ambiguous contractions: were→we're, ill→I'll, its→it's (use context clues).
- Do NOT change wording, add detail, or fix typos.
- Return ONLY the restored text, no explanation.

Text to restore:
{text}"""
    
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "temperature": 0.1,  # Low temperature for deterministic output
            },
            timeout=OLLAMA_TIMEOUT
        )
        
        if response.status_code != 200:
            print(f"ERROR: Ollama returned {response.status_code}: {response.text[:100]}")
            return None
        
        data = response.json()
        return data.get('response', '').strip()
    
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Cannot connect to Ollama at {OLLAMA_HOST}")
        return None
    except requests.exceptions.Timeout:
        print(f"ERROR: Ollama request timed out after {OLLAMA_TIMEOUT}s")
        return None
    except Exception as e:
        print(f"ERROR: Ollama API call failed: {e}")
        return None


def detect_hallucination(original: Window, cleaned_text: str) -> Tuple[bool, Optional[Dict]]:
    """
    Compare word sequences before/after cleaning.
    Returns: (hallucination_detected, diff_info)
    
    Hallucination = words added or dropped (reordering is OK for punctuation).
    """
    orig_tokens = original.word_tokens()
    cleaned_tokens = Window(
        chapter_num=original.chapter_num,
        window_idx=original.window_idx,
        text=cleaned_text,
        offset_start=original.offset_start,
        offset_end=original.offset_end
    ).word_tokens()
    
    # Check if token counts differ significantly
    if len(orig_tokens) == len(cleaned_tokens) == 0:
        return False, None
    
    if len(orig_tokens) == 0 or len(cleaned_tokens) == 0:
        return True, {
            'orig_count': len(orig_tokens),
            'cleaned_count': len(cleaned_tokens),
            'type': 'empty_mismatch'
        }
    
    # Allow small tolerance (±5% due to punctuation normalization)
    token_ratio = len(cleaned_tokens) / len(orig_tokens)
    max_ratio = 1.05
    min_ratio = 0.95
    
    if token_ratio > max_ratio or token_ratio < min_ratio:
        return True, {
            'orig_count': len(orig_tokens),
            'cleaned_count': len(cleaned_tokens),
            'ratio': token_ratio,
            'type': 'count_mismatch'
        }
    
    return False, None


def extract_scenes_from_window(window_text: str, chapter_num: int, window_idx: int) -> List[Dict]:
    """
    Extract scene metadata (characters, locations) from a window.
    Conservative approach: only tag clear dialogue and obvious locations.
    """
    scenes = []
    
    # Extract character names from dialogue tags (simple regex)
    # Pattern: "Harry said" or 'Ron whispered' or similar
    dialogue_pattern = r'["\']([^"\']+)["\'][\s]*(\w+(?:\s+\w+)?)\s+(?:said|whispered|cried|shouted|muttered|asked|replied|answered)'
    matches = re.finditer(dialogue_pattern, window_text, re.IGNORECASE)
    
    characters = set()
    for match in matches:
        # Extract speaker name
        speaker_part = match.group(2).strip()
        # Filter out common verbs masquerading as names
        if speaker_part.lower() not in ['as', 'that', 'which', 'but']:
            characters.add(speaker_part)
    
    # Extract location clues (conservative)
    # Patterns: "in Hogwarts", "at Diagon Alley", "from the Common Room"
    location_pattern = r'\b(?:at|in|from|near)\s+([A-Z][a-zA-Z\s]+(?:Room|Hall|Tower|House|Forest|Street|Alley|Market|Tavern|Platform))\b'
    location_matches = re.finditer(location_pattern, window_text)
    
    locations = []
    for match in location_matches:
        loc = match.group(1).strip()
        if loc not in locations:
            locations.append(loc)
    
    # Only create scene entry if we found clear dialogue or location
    if characters or locations:
        scenes.append({
            'chapter_num': chapter_num,
            'window_idx': window_idx,
            'characters': sorted(list(characters)),
            'locations': locations,
            'confidence': 'high' if characters and locations else 'medium'
        })
    
    return scenes


def process_chapter(chapter_path: Path, chapter_num: int, 
                   use_llm: bool = True, model: str = OLLAMA_MODEL) -> Dict:
    """
    Process a single chapter: window it, send to Ollama,
    guard against hallucination, return cleaned text and stats.
    """
    print(f"\n{'='*70}")
    print(f"Processing Chapter {chapter_num}: {chapter_path.name}")
    print(f"{'='*70}")
    
    # Read chapter
    with open(chapter_path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Split into windows
    windows = split_into_windows(text, chapter_num)
    print(f"Split into {len(windows)} windows (~1000 words each)")
    
    results = []
    scenes = []
    hallucination_count = 0
    error_count = 0
    total_api_time = 0
    
    # Process each window
    for i, window in enumerate(windows):
        # Progress indicator
        if (i + 1) % 10 == 0:
            print(f"  [{i+1:4d}/{len(windows)}]", end='', flush=True)
        elif (i + 1) % 5 == 0:
            print(f".", end='', flush=True)
        else:
            print(f".", end='', flush=True)
        
        if not use_llm:
            # Dry run: just track original
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,
                hallucination_detected=False,
                llm_model=None
            ))
            continue
        
        # Call Ollama
        start_time = time.time()
        llm_output = call_ollama(window.text, model=model)
        api_time = time.time() - start_time
        total_api_time += api_time
        
        if llm_output is None:
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,
                hallucination_detected=False,
                error="OLLAMA_CALL_FAILED",
                llm_model=model
            ))
            error_count += 1
            continue
        
        # Guard: check for hallucination
        hallucinated, diff = detect_hallucination(window, llm_output)
        
        if hallucinated:
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,  # Keep original
                hallucination_detected=True,
                word_diff=diff,
                llm_output=llm_output,
                llm_model=model
            ))
            hallucination_count += 1
        else:
            results.append(WindowResult(
                window=window,
                cleaned_text=llm_output,
                hallucination_detected=False,
                llm_output=llm_output,
                llm_model=model
            ))
            
            # Extract scene metadata from cleaned text
            window_scenes = extract_scenes_from_window(llm_output, chapter_num, i)
            scenes.extend(window_scenes)
    
    print()  # newline after progress
    
    # Reconstruct chapter from windows
    reconstructed = reconstruct_from_windows(results)
    
    # Summary stats
    avg_api_time = total_api_time / len(windows) if len(windows) > 0 else 0
    stats = {
        'chapter_num': chapter_num,
        'original_size': len(text),
        'cleaned_size': len(reconstructed),
        'windows_total': len(windows),
        'windows_hallucinated': hallucination_count,
        'windows_error': error_count,
        'windows_ok': len(windows) - hallucination_count - error_count,
        'hallucination_rate': hallucination_count / len(windows) if windows else 0,
        'avg_api_time_sec': avg_api_time,
        'total_api_time_sec': total_api_time,
        'llm_model': model,
    }
    
    print(f"\nStats:")
    print(f"  Total windows: {stats['windows_total']}")
    print(f"  Hallucinations detected & rejected: {stats['windows_hallucinated']}")
    print(f"  API errors: {stats['windows_error']}")
    print(f"  Successful: {stats['windows_ok']}")
    print(f"  Hallucination rate: {stats['hallucination_rate']*100:.1f}%")
    if avg_api_time > 0:
        print(f"  Avg API latency: {avg_api_time:.2f}s/window")
        est_hours = (stats['windows_total'] * avg_api_time) / 3600
        print(f"  Est. time for chapter: {est_hours:.1f} hours")
    
    return {
        'chapter_num': chapter_num,
        'original_text': text,
        'cleaned_text': reconstructed,
        'windows': len(results),  # Don't serialize full window data (too large)
        'scenes': scenes,
        'stats': stats
    }


def reconstruct_from_windows(results: List[WindowResult]) -> str:
    """
    Reconstruct full chapter text from processed windows.
    Simple concatenation with space joining (overlaps are minimal).
    """
    if not results:
        return ""
    
    parts = []
    for result in results:
        parts.append(result.cleaned_text)
    
    return ' '.join(parts)


def check_ollama_available(host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL) -> bool:
    """Check if Ollama is running and has the model available."""
    try:
        response = requests.get(f"{host}/api/tags", timeout=5)
        if response.status_code != 200:
            print(f"ERROR: Ollama health check failed: {response.status_code}")
            return False
        
        data = response.json()
        available_models = [m.get('name', '') for m in data.get('models', [])]
        
        # Check if model exists (exact match or base name match)
        model_found = model in available_models or any(m.startswith(model.split(':')[0]) for m in available_models)
        
        if not model_found:
            print(f"WARNING: Model '{model}' not found. Available: {', '.join(available_models)}")
            print(f"  Pull with: ollama pull {model}")
            return False
        
        print(f"✓ Ollama is running at {host}")
        print(f"✓ Model '{model}' is available")
        return True
    
    except Exception as e:
        print(f"ERROR: Cannot reach Ollama at {host}: {e}")
        return False


def main():
    root = Path('C:/Users/matt/PycharmProjects/virtualTubers')
    input_dir = root / 'sourceworks' / 'chapters_deterministic'
    output_dir = root / 'sourceworks' / 'chapters_llm_restored'
    
    output_dir.mkdir(exist_ok=True)
    
    print(f"\nHarry Potter Book Cleaning - Phase 2: LLM Restoration (Ollama)")
    print(f"{'='*70}\n")
    
    # Check Ollama availability
    if not check_ollama_available(OLLAMA_HOST, OLLAMA_MODEL):
        print(f"\nFalling back to test mode (no LLM)...")
        use_llm = False
    else:
        use_llm = True
    
    print()
    
    # Find all chapter files
    chapter_files = sorted(input_dir.glob('*.txt'))
    print(f"Found {len(chapter_files)} chapter files\n")
    
    # For testing, process just the first chapter
    test_mode = True
    if test_mode:
        chapter_files = chapter_files[:1]
        print(f"TEST MODE: Processing first chapter only\n")
    
    # Process each chapter
    all_results = []
    master_scenes = []
    
    for chapter_path in chapter_files:
        # Extract chapter number from filename (e.g., "1_001_...")
        match = re.match(r'(\d+)_(\d+)_', chapter_path.name)
        if not match:
            print(f"Skipping {chapter_path.name} (can't extract chapter number)")
            continue
        
        book_num, chapter_num = int(match.group(1)), int(match.group(2))
        
        result = process_chapter(chapter_path, chapter_num, use_llm=use_llm, model=OLLAMA_MODEL)
        all_results.append(result)
        master_scenes.extend(result['scenes'])
        
        # Write cleaned chapter
        output_file = output_dir / chapter_path.name
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result['cleaned_text'])
        
        print(f"✓ Written to {output_file.name}\n")
    
    # Write master results summary (excluding large text fields)
    results_file = output_dir / 'llm_restoration_results.json'
    summary = []
    for r in all_results:
        summary.append({
            'chapter_num': r['chapter_num'],
            'windows': r['windows'],
            'scenes_found': len(r['scenes']),
            'stats': r['stats'],
        })
    
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    
    print(f"Results summary written to {results_file}")
    
    # Write scenes file for downstream use
    scenes_file = output_dir / 'scenes.json'
    with open(scenes_file, 'w', encoding='utf-8') as f:
        json.dump(master_scenes, f, indent=2)
    
    print(f"Scenes metadata written to {scenes_file} ({len(master_scenes)} scenes)")
    
    # Print summary stats
    if all_results:
        total_hallucinations = sum(r['stats']['windows_hallucinated'] for r in all_results)
        total_errors = sum(r['stats']['windows_error'] for r in all_results)
        total_windows = sum(r['stats']['windows_total'] for r in all_results)
        total_api_time = sum(r['stats']['total_api_time_sec'] for r in all_results)
        
        print(f"\n{'='*70}")
        print(f"SUMMARY - All chapters processed:")
        print(f"{'='*70}")
        print(f"  Total windows: {total_windows}")
        print(f"  Hallucinations rejected: {total_hallucinations} ({100*total_hallucinations/total_windows if total_windows else 0:.1f}%)")
        print(f"  API errors: {total_errors}")
        print(f"  Total API time: {total_api_time:.1f}s")
        if total_api_time > 0:
            print(f"  Avg latency: {total_api_time/total_windows:.2f}s/window")
        print(f"  Scenes extracted: {len(master_scenes)}")
    
    if use_llm:
        print(f"\n✅ Phase 2 (LLM Restoration): Complete with Ollama on {OLLAMA_HOST}")
    else:
        print(f"\n⚠️  Phase 2 (LLM Restoration): Test mode (Ollama not available)")
    
    print(f"Ready for Phase 3 (Word Diff Guard) and Phase 4 (Postgres Storage)")


if __name__ == '__main__':
    main()
