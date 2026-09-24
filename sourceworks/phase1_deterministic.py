#!/usr/bin/env python3
"""
Phase 1: Deterministic cleaning pass
- Chapter splitting by title matching
- Unambiguous contraction restoration
- Spacing & sentence fixes
No LLM involved.
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Tuple

# Unambiguous contractions - safe to restore deterministically
CONTRACTIONS_DICT = {
    # NOT contractions
    'didnt': "didn't",
    'dont': "don't",
    'wont': "won't",
    'cant': "can't",
    'shouldnt': "shouldn't",
    'couldnt': "couldn't",
    'wouldnt': "wouldn't",
    'isnt': "isn't",
    'arent': "aren't",
    'wasnt': "wasn't",
    'werent': "weren't",
    'hasnt': "hasn't",
    'havent': "haven't",
    'hadnt': "hadn't",
    'doesnt': "doesn't",
    'mustnt': "mustn't",
    
    # WILL contractions
    'ill': "I'll",
    'well': "we'll",
    'shell': "she'll",
    'hell': "he'll",
    'theyll': "they'll",
    'youll': "you'll",
    'itll': "it'll",
    
    # HAVE contractions
    'ive': "I've",
    'weve': "we've",
    'theyve': "they've",
    'youve': "you've",
    
    # WOULD contractions
    'id': "I'd",
    'wed': "we'd",
    'shed': "she'd",
    'hed': "he'd",
    'theyd': "they'd",
    'youd': "you'd",
    
    # AM/IS contractions
    'im': "I'm",
    'hes': "he's",
    'shes': "she's",
    'thats': "that's",
    'whats': "what's",
    'whos': "who's",
    'lets': "let's",
}

def load_chapters_metadata(json_path: Path) -> Dict[int, str]:
    """Load chapter titles from JSON metadata."""
    with open(json_path, 'r', encoding='utf-8') as f:
        chapters = json.load(f)
    return {ch['chapter_number']: ch['title'] for ch in chapters}


def normalize_for_matching(s: str) -> str:
    """Normalize string for chapter title matching."""
    # Remove apostrophes and convert to uppercase
    return re.sub(r"['''`\u2018\u2019]", "", s).upper()


def extract_chapters(text: str, chapters_metadata: Dict[int, str]) -> Dict[int, Tuple[str, int, int]]:
    """
    Extract chapter content and boundaries by matching titles.
    Returns: {chapter_num: (content, offset_start, offset_end), ...}
    """
    chapters = {}
    sorted_ch_nums = sorted(chapters_metadata.keys())
    
    print(f"Extracting {len(sorted_ch_nums)} chapters by title matching...")
    
    for i, ch_num in enumerate(sorted_ch_nums):
        title = chapters_metadata[ch_num]
        search_pattern = normalize_for_matching(title)
        
        # Find chapter title in text
        start_idx = text.find(search_pattern)
        if start_idx == -1:
            print(f"  ⚠ Chapter {ch_num} '{title}' not found in text")
            continue
        
        # Find next chapter boundary
        end_idx = -1
        if i + 1 < len(sorted_ch_nums):
            next_ch_num = sorted_ch_nums[i + 1]
            next_title = chapters_metadata[next_ch_num]
            next_pattern = normalize_for_matching(next_title)
            end_idx = text.find(next_pattern, start_idx + len(search_pattern))
        
        if end_idx == -1:
            end_idx = len(text)
        
        content = text[start_idx:end_idx].strip()
        chapters[ch_num] = (content, start_idx, end_idx)
        print(f"  ✓ Chapter {ch_num:3d}: {len(content):,} chars")
    
    return chapters


def restore_unambiguous_contractions(text: str) -> str:
    """
    Restore contractions from a fixed dictionary.
    Uses word boundaries to avoid false matches.
    """
    for broken, restored in CONTRACTIONS_DICT.items():
        # Match word boundary + broken form + word boundary or punctuation
        pattern = r'\b' + broken + r'(?=[^a-z]|$)'
        text = re.sub(pattern, restored, text, flags=re.IGNORECASE)
    
    return text


def fix_spacing(text: str) -> str:
    """Fix common spacing issues around punctuation."""
    # " ." → "."
    text = re.sub(r'\s+\.', '.', text)
    # " ," → ","
    text = re.sub(r'\s+,', ',', text)
    # " !" → "!"
    text = re.sub(r'\s+!', '!', text)
    # " ?" → "?"
    text = re.sub(r'\s+\?', '?', text)
    # Multiple spaces → single space
    text = re.sub(r' {2,}', ' ', text)
    # Multiple newlines → double newline (paragraph breaks)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text


def process_chapter_deterministic(text: str) -> str:
    """Apply Phase 1 deterministic cleaning to a chapter."""
    text = restore_unambiguous_contractions(text)
    text = fix_spacing(text)
    return text


def main():
    # Paths
    root = Path('C:/Users/matt/PycharmProjects/virtualTubers')
    text_path = root / 'sourceworks' / 'Harry_Potter_all_books_preprocessed.txt'
    metadata_path = root / 'sourceworks' / 'harryPotterAllChapterNames.json'
    output_dir = root / 'sourceworks' / 'chapters_deterministic'
    
    output_dir.mkdir(exist_ok=True)
    
    # Load inputs
    print("Loading metadata...")
    chapters_metadata = load_chapters_metadata(metadata_path)
    
    print("Loading text file...")
    with open(text_path, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    
    print(f"Text size: {len(text):,} characters\n")
    
    # Extract chapters
    chapters = extract_chapters(text, chapters_metadata)
    print(f"Successfully extracted {len(chapters)} chapters\n")
    
    # Process and write
    print("Processing and writing chapters...")
    chapter_metadata_out = []
    
    for ch_num in sorted(chapters.keys()):
        content, offset_start, offset_end = chapters[ch_num]
        
        # Deterministic cleaning
        cleaned = process_chapter_deterministic(content)
        
        # Infer book number
        if ch_num <= 17:
            book_num = 1
        elif ch_num <= 35:
            book_num = 2
        elif ch_num <= 57:
            book_num = 3
        elif ch_num <= 94:
            book_num = 4
        elif ch_num <= 132:
            book_num = 5
        elif ch_num <= 162:
            book_num = 6
        else:
            book_num = 7
        
        title = chapters_metadata[ch_num]
        
        # Write chapter file
        filename = f"{book_num}_{ch_num:03d}_{title.replace(chr(39), '')}.txt"
        filepath = output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(cleaned)
        
        # Track metadata
        chapter_metadata_out.append({
            'book_number': book_num,
            'chapter_number': ch_num,
            'title': title,
            'content_offset_start': offset_start,
            'content_offset_end': offset_end,
            'filename': filename,
            'cleaned_size': len(cleaned),
        })
        
        print(f"  ✓ {book_num}_{ch_num:03d} ({len(cleaned):,} chars)")
    
    # Write metadata
    metadata_out_path = output_dir / 'chapter_metadata.json'
    with open(metadata_out_path, 'w', encoding='utf-8') as f:
        json.dump(chapter_metadata_out, f, indent=2, ensure_ascii=False)
    
    print(f"\nMetadata written to {metadata_out_path}")
    print(f"All chapters written to {output_dir}")
    print(f"Total chapters processed: {len(chapter_metadata_out)}")


if __name__ == '__main__':
    main()
