#!/usr/bin/env python3
import re
from pathlib import Path

# Read the chapter names file
chapter_names_path = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/harryPotterChapterNamesRaw.txt")
with open(chapter_names_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Parse chapter names
chapters = {}  # {chapter_number: title}

for line in lines:
    line = line.strip()
    if not line or 'Chapters overall' in line or "Harry Potter and the" in line:
        continue
    
    # Split by tab
    fields = line.split('\t')
    if len(fields) >= 3:
        try:
            chapter_num = int(fields[0].strip())
            title = fields[2].strip()
            chapters[chapter_num] = title
        except ValueError:
            pass

print(f"Parsed {len(chapters)} chapters")

# Read the main text file
text_path = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/Harry_Potter_all_books_preprocessed.txt")
with open(text_path, 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

print(f"Text file size: {len(text):,} characters\n")

def find_chapter(text, title):
    """Find chapter in text, trying multiple normalization strategies"""
    # Handle Unicode apostrophes: ' (ASCII), ' (U+2018), ' (U+2019), ` (U+2018)
    search_patterns = [
        # Standard: uppercase with apostrophes removed (including Unicode variants)
        re.sub(r"['''`\u2018\u2019]", "", title).upper(),
        # With hyphens removed too
        re.sub(r"['''`\u2018\u2019\-]", "", title).upper(),
        # With spaces removed (for cases like "LIGHTNINGSTRUCK")
        re.sub(r"['''`\u2018\u2019\-\s]", "", title).upper(),
    ]
    
    for pattern in search_patterns:
        idx = text.find(pattern)
        if idx != -1:
            return idx
    
    return -1

# Create output directory
output_dir = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/chapters")
output_dir.mkdir(exist_ok=True)

# Find chapter markers in text and split
chapter_texts = {}
sorted_chapters = sorted(chapters.keys())

print("Finding chapters in text...")
for i, ch_num in enumerate(sorted_chapters):
    title = chapters[ch_num]
    start_idx = find_chapter(text, title)
    
    if start_idx == -1:
        print(f"  X Chapter {ch_num:3d}: '{title}' NOT FOUND")
        continue
    
    # Find the next chapter marker
    next_idx = -1
    if i + 1 < len(sorted_chapters):
        next_title = chapters[sorted_chapters[i + 1]]
        next_idx = find_chapter(text, next_title)
        if next_idx != -1 and next_idx > start_idx:
            # Use this next_idx only if it's after current start
            pass
        else:
            next_idx = -1
    
    # Extract chapter text
    if next_idx > 0:
        chapter_text = text[start_idx:next_idx]
    else:
        chapter_text = text[start_idx:]
    
    chapter_texts[ch_num] = chapter_text.strip()
    size_kb = len(chapter_text) / 1024
    print(f"  ✓ Chapter {ch_num:3d}: {size_kb:7.1f} KB")

print(f"\nSuccessfully extracted {len(chapter_texts)} chapters")

# Write individual chapter files
print(f"\nWriting chapter files to {output_dir}...")
for ch_num in sorted(chapter_texts.keys()):
    title = chapters[ch_num]
    # Sanitize filename
    safe_title = re.sub(r"[<>:\"/\\|?*''-]", "_", title)
    filename = f"{ch_num:03d}_{safe_title}.txt"
    filepath = output_dir / filename
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(chapter_texts[ch_num])

print("Done writing chapter files.")

# Create chapter names file in JSON format (best for LLM consumption)
import json

chapter_list = []
for ch_num in sorted_chapters:
    if ch_num in chapters:
        chapter_list.append({
            "chapter_number": ch_num,
            "title": chapters[ch_num]
        })

json_output = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/harryPotterAllChapterNames.json")
with open(json_output, 'w', encoding='utf-8') as f:
    json.dump(chapter_list, f, indent=2, ensure_ascii=False)

print(f"\nCreated: {json_output}")

# Also create a simple text list version
txt_output = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/harryPotterAllChapterNames.txt")
with open(txt_output, 'w', encoding='utf-8') as f:
    f.write("HARRY POTTER - ALL CHAPTERS\n")
    f.write("=" * 70 + "\n\n")
    for ch in chapter_list:
        f.write(f"{ch['chapter_number']:3d}. {ch['title']}\n")

print(f"Created: {txt_output}")

# Create a markdown version for readability
md_output = Path("C:/Users/matt/PycharmProjects/virtualTubers/sourceworks/harryPotterAllChapterNames.md")
with open(md_output, 'w', encoding='utf-8') as f:
    f.write("# Harry Potter - All Chapters\n\n")
    current_book = ""
    for ch in chapter_list:
        # Infer book from chapter number ranges
        ch_num = ch['chapter_number']
        if ch_num <= 17:
            book = "Philosopher's Stone"
        elif ch_num <= 35:
            book = "Chamber of Secrets"
        elif ch_num <= 57:
            book = "Prisoner of Azkaban"
        elif ch_num <= 94:
            book = "Goblet of Fire"
        elif ch_num <= 132:
            book = "Order of the Phoenix"
        elif ch_num <= 162:
            book = "Half-Blood Prince"
        else:
            book = "Deathly Hallows"
        
        if book != current_book:
            f.write(f"\n## {book}\n\n")
            current_book = book
        
        f.write(f"- **{ch['chapter_number']}:** {ch['title']}\n")

print(f"Created: {md_output}")

print(f"\nTotal chapters: {len(chapter_list)}")
print(f"Extracted: {len(chapter_texts)}")
print("All done!")
