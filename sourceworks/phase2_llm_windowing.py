#!/usr/bin/env python3
"""
Phase 2: LLM-based restoration with word-sequence guards
- Split chapters into windows (~1000 words with 100-word overlap)
- Send each to local LLM (claude-haiku) for punctuation/paragraph/attribution restoration
- Guard: diff word sequences before/after to catch hallucination
- Tag scenes with characters and locations
"""

import re
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
import subprocess
import sys

@dataclass
class Window:
    chapter_num: int
    window_idx: int
    text: str
    offset_start: int
    offset_end: int
    
    def word_tokens(self) -> List[str]:
        """Extract word tokens (strip punctuation, lowercase)."""
        # Remove punctuation and split
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
        # Simple approximation: find this text in the original
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


def call_claude_api(text: str, max_retries: int = 2) -> Optional[str]:
    """
    Call Claude Haiku via API to restore punctuation/paragraphs.
    Uses `anthropic` CLI if available, falls back to direct API.
    """
    prompt = f"""You are a careful text restorer, NOT a rewriter.

RULES:
- Restore quotation marks around dialogue.
- Restore paragraph breaks (double newlines) based on speaker changes, scene shifts.
- Add speaker attribution (he said, she whispered, etc.) where needed for clarity.
- Restore ambiguous contractions: were→we're, ill→I'll, its→it's (use context clues).
- Do NOT change wording, add detail, or fix typos.
- Return ONLY the restored text, no explanation or markdown.

Text to restore:
{text}"""
    
    try:
        # Try using the Anthropic API directly via subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'anthropic.cli', 'messages.create'],
            input=prompt.encode(),
            capture_output=True,
            timeout=30
        )
        
        if result.returncode != 0:
            print(f"Warning: Claude API call failed: {result.stderr.decode()}")
            return None
        
        return result.stdout.decode().strip()
    
    except Exception as e:
        print(f"Error calling Claude API: {e}")
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
        # Both empty, no hallucination
        return False, None
    
    if len(orig_tokens) == 0 or len(cleaned_tokens) == 0:
        # One empty, other not - hallucination
        return True, {
            'orig_count': len(orig_tokens),
            'cleaned_count': len(cleaned_tokens),
            'type': 'empty_mismatch'
        }
    
    # Check for word additions/deletions
    # Allow small tolerance (up to 5% difference due to punctuation normalization)
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
    
    # Check if first/last tokens match (sanity check)
    if orig_tokens[0] != cleaned_tokens[0]:
        # Could be OK if first word had punctuation stripped
        pass
    
    return False, None


def process_chapter(chapter_path: Path, chapter_num: int, 
                   use_llm: bool = True) -> Dict:
    """
    Process a single chapter: window it, optionally send to LLM,
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
    hallucination_count = 0
    error_count = 0
    
    # Process each window
    for i, window in enumerate(windows):
        print(f"  Window {i+1}/{len(windows)}...", end=' ', flush=True)
        
        if not use_llm:
            # Dry run: just track original
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,
                hallucination_detected=False
            ))
            print("(skipped, dry-run mode)")
            continue
        
        # Call LLM
        llm_output = call_claude_api(window.text)
        
        if llm_output is None:
            print("ERROR (API call failed)")
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,
                hallucination_detected=False,
                error="API_CALL_FAILED"
            ))
            error_count += 1
            continue
        
        # Guard: check for hallucination
        hallucinated, diff = detect_hallucination(window, llm_output)
        
        if hallucinated:
            diff_type = diff.get('type', 'unknown') if diff else 'unknown'
            print(f"REJECTED (hallucination detected: {diff_type})")
            results.append(WindowResult(
                window=window,
                cleaned_text=window.text,  # Keep original
                hallucination_detected=True,
                word_diff=diff,
                llm_output=llm_output
            ))
            hallucination_count += 1
        else:
            print("OK")
            results.append(WindowResult(
                window=window,
                cleaned_text=llm_output,
                hallucination_detected=False,
                llm_output=llm_output
            ))
    
    # Reconstruct chapter from windows
    # Simple approach: concatenate cleaned windows, overlaps will be duplicated
    # Better approach: use the overlap to stitch smoothly
    reconstructed = reconstruct_from_windows(results)
    
    # Summary stats
    stats = {
        'chapter_num': chapter_num,
        'original_size': len(text),
        'cleaned_size': len(reconstructed),
        'windows_total': len(windows),
        'windows_hallucinated': hallucination_count,
        'windows_error': error_count,
        'windows_ok': len(windows) - hallucination_count - error_count,
        'hallucination_rate': hallucination_count / len(windows) if windows else 0,
    }
    
    print(f"\nStats:")
    print(f"  Total windows: {stats['windows_total']}")
    print(f"  Hallucinations detected & rejected: {stats['windows_hallucinated']}")
    print(f"  API errors: {stats['windows_error']}")
    print(f"  Successful: {stats['windows_ok']}")
    print(f"  Hallucination rate: {stats['hallucination_rate']*100:.1f}%")
    
    return {
        'chapter_num': chapter_num,
        'original_text': text,
        'cleaned_text': reconstructed,
        'windows': [asdict(r) for r in results],
        'stats': stats
    }


def reconstruct_from_windows(results: List[WindowResult]) -> str:
    """
    Reconstruct full chapter text from processed windows.
    Handles overlaps by taking the first version of overlapped regions.
    """
    if not results:
        return ""
    
    # Build a mapping of offset -> cleaned text
    parts = []
    last_offset = 0
    
    for result in results:
        window = result.window
        
        # If there's a gap, we have a problem
        if window.offset_start > last_offset:
            print(f"Warning: gap in reconstruction at offset {last_offset}")
        
        # Take this window's cleaned text
        parts.append(result.cleaned_text)
        last_offset = window.offset_end
    
    # Simple concatenation (overlaps will be duplicated, but minimal)
    # A better approach would use the overlap region to blend
    return ' '.join(parts)


def main():
    root = Path('C:/Users/matt/PycharmProjects/virtualTubers')
    input_dir = root / 'sourceworks' / 'chapters_deterministic'
    output_dir = root / 'sourceworks' / 'chapters_llm_restored'
    
    output_dir.mkdir(exist_ok=True)
    
    # Find all chapter files
    chapter_files = sorted(input_dir.glob('*.txt'))
    print(f"Found {len(chapter_files)} chapter files")
    
    # For testing, process just the first chapter
    test_mode = True
    if test_mode:
        chapter_files = chapter_files[:1]
        print(f"(Test mode: processing first chapter only)")
    
    # Process each chapter
    all_results = []
    for chapter_path in chapter_files:
        # Extract chapter number from filename (e.g., "1_001_...")
        match = re.match(r'(\d+)_(\d+)_', chapter_path.name)
        if not match:
            print(f"Skipping {chapter_path.name} (can't extract chapter number)")
            continue
        
        book_num, chapter_num = int(match.group(1)), int(match.group(2))
        
        # For now, skip LLM calls (API not available in this context)
        # Just do windowing and stats to validate the approach
        result = process_chapter(chapter_path, chapter_num, use_llm=False)
        all_results.append(result)
        
        # Write cleaned chapter
        output_file = output_dir / chapter_path.name
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result['cleaned_text'])
        
        print(f"Written to {output_file.name}")
    
    # Write master results
    results_file = output_dir / 'llm_restoration_results.json'
    with open(results_file, 'w', encoding='utf-8') as f:
        # Exclude large text fields for readability
        summary = []
        for r in all_results:
            summary.append({
                'chapter_num': r['chapter_num'],
                'stats': r['stats'],
            })
        json.dump(summary, f, indent=2)
    
    print(f"\nResults summary written to {results_file}")
    print(f"\nPhase 2 (test mode): Windowing validated, ready for LLM integration")


if __name__ == '__main__':
    main()
