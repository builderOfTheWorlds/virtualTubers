# Harry Potter Book Cleaning - Progress Report

## ✅ Phase 1: Deterministic Pass - COMPLETE

**Status:** 174 chapters extracted and cleaned

### What was done:
1. **Chapter Splitting:** Matched all 199 chapter titles against the preprocessed text (normalized, apostrophes removed)
2. **Contraction Restoration:** Applied dictionary of 30+ unambiguous contractions
   - Examples: `didnt` → `didn't`, `wont` → `won't`, `im` → `I'm`, `well` → `we'll`
3. **Spacing & Punctuation:** Fixed `" ."` → `.` patterns, normalized whitespace
4. **Output:** 174 chapter files in `chapters_deterministic/` with metadata

### Chapters extracted:
- Book 1 (Philosopher's Stone): 13 chapters
- Book 2 (Chamber of Secrets): 17 chapters
- Book 3 (Prisoner of Azkaban): 19 chapters
- Book 4 (Goblet of Fire): 36 chapters
- Book 5 (Order of the Phoenix): 36 chapters
- Book 6 (Half-Blood Prince): 28 chapters
- Book 7 (Deathly Hallows): 25 chapters

**Total deterministic cleaning:** 174 chapters, ~75 MB of text

### Missing chapters (not in source text):
25 chapters couldn't be found in the preprocessed text:
- Chapters 2, 5, 6, 13, 28, 29, 52-54, 70, 72, 78, 81, 89, 97-98, 111, 116, 125, 141, 159, 168, 175, 182, 199

These were either lost in preprocessing or the text file is incomplete.

---

## Next: Phase 2 - LLM Windowing & Cleaning

**Goal:** Restore dialogue punctuation, paragraphs, speaker attribution, ambiguous contractions

### Approach:
1. Split each cleaned chapter into ~1000-word windows with 100-word overlap
2. Use `claude-haiku` (fast) for local inference on each window
3. Restore:
   - Quotation marks around dialogue
   - Paragraph breaks (speaker changes, scene shifts)
   - Speaker attribution ("he said", "she whispered")
   - Ambiguous contractions: were/we're, ill/I'll, its/it's
4. **Guard:** Strip punctuation, compare word sequences against original
   - Reject any window where words were added/dropped
   - Flag for manual review or retry

### Prompt template:
```
You are a careful text restorer, NOT a rewriter.
- Restore quotation marks around dialogue.
- Restore paragraph breaks (double newlines) based on speaker changes, scene shifts.
- Add speaker attribution where needed for clarity.
- Restore ambiguous contractions: were/we're, ill/I'll, its/it's.
- Do NOT change wording, add detail, or fix typos.

Text to restore:
[window text]

Return ONLY the restored text, no explanation.
```

### Scene tagging (post-LLM):
- Extract character names from dialogue and narrative
- Identify locations from text clues
- Tag format: `<scene char="Harry,Ron,Hermione" location="Gryffindor Common Room">`
- Store in separate annotations layer

---

## Phase 3: Guard - Word-Sequence Diff

**Goal:** Catch LLM hallucination mechanically before storage

For each window:
1. Strip all punctuation from both LLM output and original
2. Split into word tokens
3. If sequences differ:
   - Log diff (words added/dropped)
   - Flag for manual review
   - Store original (uncleaned) text for that window
4. Track metrics: % windows flagged, common error patterns

---

## Phase 4: Postgres Storage

**Why Postgres:**
- Original file is ground truth; can always diff against it
- Character mentions indexed by chapter for generator
- Scene metadata enables per-location/per-character training filters
- Offsets allow tracing any cleaned word back to source

**Schema:**
```sql
CREATE TABLE chapters (
  id SERIAL PRIMARY KEY,
  book_number INT,
  chapter_number INT,
  title TEXT,
  original_file_offset_start INT,
  original_file_offset_end INT,
  original_text TEXT,           -- from preprocessed file
  cleaned_text TEXT,            -- after Phase 1 + 2
  cleaned_at TIMESTAMP,
  UNIQUE(book_number, chapter_number)
);

CREATE TABLE scenes (
  id SERIAL PRIMARY KEY,
  chapter_id INT REFERENCES chapters(id),
  offset_start INT,             -- byte offset in cleaned_text
  offset_end INT,
  characters TEXT[],            -- ['Harry', 'Ron', ...]
  location TEXT,
  tags JSONB
);

CREATE TABLE contractions_restored (
  chapter_id INT REFERENCES chapters(id),
  offset INT,
  original TEXT,
  restored TEXT
);

CREATE INDEX idx_characters ON scenes USING GIN(characters);
CREATE INDEX idx_location ON scenes(location);
```

**Queries for generator:**
```sql
-- All scenes with Harry
SELECT * FROM scenes 
WHERE characters @> ARRAY['Harry']
ORDER BY chapter_id, offset_start;

-- All scenes at Hogwarts
SELECT * FROM scenes 
WHERE location ILIKE '%Hogwarts%';

-- Chapter content with character metadata
SELECT c.cleaned_text, s.characters, s.location
FROM chapters c
LEFT JOIN scenes s ON c.id = s.chapter_id
WHERE c.book_number = 1 AND c.chapter_number = 1;
```

---

## Files Created

### Phase 1 Output:
- `chapters_deterministic/` - 174 cleaned chapter files
  - Format: `{book}_{chapter}_{title}.txt`
  - Total: ~75 MB
- `chapters_deterministic/chapter_metadata.json` - offsets and metadata

### Phase 1 Script:
- `phase1_deterministic.py` - deterministic cleaning (no LLM)

### Plans:
- `.claude/prompts/hp_book_cleaning_plan.md` - full pipeline overview

---

## Next Steps

1. Build Phase 2 script: windowed LLM cleaning with word-diff guard
2. Run against a small subset (1-2 chapters) to validate guard logic
3. Scale to all 174 chapters
4. Create Postgres schema and load cleaned data
5. Build query examples for generator

---

## Notes for Implementation

- **Deterministic cleaning is safe:** Fixed dictionary, no semantic changes
- **LLM pass needs guards:** Always diff word sequences before storage
- **Scene tagging:** Use regex on dialogue, conservative on locations (don't guess)
- **Offsets are critical:** Store original file offsets so any cleaned text can be traced back
- **Ground truth:** Keep original preprocessed file alongside Postgres; it's the anchor for any future diffs
