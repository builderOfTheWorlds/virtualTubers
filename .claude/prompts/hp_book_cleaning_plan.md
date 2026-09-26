# Harry Potter Book Cleaning Pipeline

## Overview
Deterministic + local LLM pipeline with mechanical guards against hallucination. Preserves canon by diffing word sequences and keeping the original as ground truth in Postgres.

## Phase 1: Deterministic Pass (No LLM)
**Goal:** Split by chapters, restore basic spacing/contractions, fix sentence boundaries

### 1a. Chapter Splitting
- Match all 199 chapter titles (normalized: uppercase, apostrophes removed) against ALL-CAPS runs in text
- Use `harryPotterAllChapterNames.json` as reference
- Extract chapter boundaries and content into separate strings
- Log any title not found or ambiguous matches

### 1b. Contraction Restoration (Unambiguous)
Dictionary-based, deterministic:
- didnt → didn't
- dont → don't
- wont → won't
- cant → can't
- wouldnt → wouldn't
- couldnt → couldn't
- shouldnt → shouldn't
- isnt → isn't
- arent → aren't
- wasnt → wasn't
- werent → weren't
- hasnt → hasn't
- havent → haven't
- hadnt → hadn't
- doesnt → doesn't
- dont → don't
- etc.

### 1c. Spacing & Sentence Fixing
- Fix `" ."` → `.` (quote+period spacing)
- Fix `" ,"` → `,`
- Fix `" !"` → `!`
- Fix `" ?"` → `?`
- Re-split sentences on `[.!?]` boundaries

**Output:** 
- `chapters_deterministic/` dir with one file per chapter
- Metadata: `chapter_metadata.json` with boundaries, title, book number

---

## Phase 2: Local LLM Pass (Per-Chapter, Windowed)
**Goal:** Restore punctuation, paragraphs, dialogue attribution, ambiguous contractions

### 2a. Window Strategy
- Split each chapter into windows of ~1000 words (or scene breaks)
- Process each window independently with `claude-haiku` (fast, local)
- Include 100-word overlap for context

### 2b. LLM Prompt
```
You are a careful text restorer, NOT a rewriter.
- Restore quotation marks around dialogue.
- Restore paragraph breaks (double newlines) based on speaker changes, scene shifts.
- Add speaker attribution (he said, she whispered) where needed for clarity.
- Restore ambiguous contractions: "were" ↔ "we're", "ill" ↔ "I'll", "its" ↔ "it's".
- Do NOT change wording, add detail, or fix typos. Only restore punctuation and structure.

Text to restore:
[window text]

Return ONLY the restored text, no explanation.
```

### 2c. Scene Tagging (Post-LLM)
- Regex to extract character dialogue and locations
- Tag format: `<scene char="Harry,Ron,Hermione" location="Gryffindor Common Room">`
- Store in annotation layer, not in text itself

**Output:**
- `chapters_cleaned/` dir with restored text
- `annotations.json` with scene tags and character presence by chapter offset

---

## Phase 3: Guard: Word-Sequence Diff
**Goal:** Catch hallucination mechanically

For each window:
1. Strip all punctuation from LLM output
2. Split into words
3. Strip all punctuation from original window
4. Split into words
5. If word sequences differ:
   - Log the diff
   - Flag window for manual review OR retry with different prompt
   - Store original (unclean) text for that window

**Metrics to track:**
- Words added (hallucination)
- Words dropped (deletion)
- Words reordered (should not happen)

---

## Phase 4: Postgres Storage
**Schema:**
```sql
CREATE TABLE chapters (
  id SERIAL PRIMARY KEY,
  book_number INT,
  chapter_number INT,
  title TEXT,
  content_offset_start INT,  -- byte offset in original file
  content_offset_end INT,
  original_text TEXT,         -- unmodified from file
  cleaned_text TEXT,          -- after Phase 1 + 2
  cleaned_at TIMESTAMP,
  UNIQUE(book_number, chapter_number)
);

CREATE TABLE scenes (
  id SERIAL PRIMARY KEY,
  chapter_id INT REFERENCES chapters(id),
  offset_start INT,
  offset_end INT,
  characters TEXT[],          -- ['Harry', 'Ron', ...]
  location TEXT,
  tags JSONB                  -- extensible
);

CREATE TABLE contractions_restored (
  chapter_id INT REFERENCES chapters(id),
  offset INT,
  original TEXT,
  restored TEXT
);
```

**Why?**
- Original file is ground truth; can always diff against it
- Character mentions indexed by chapter for generator
- Scene metadata enables per-location/per-character training filters
- Offset storage allows tracing any cleaned word back to source

---

## Implementation Order
1. ✅ Deterministic chapter split + contraction dict
2. ✅ Chapter boundary extraction (with logging)
3. ✅ LLM windowing + prompt
4. ✅ Word-sequence diff guard
5. ✅ Postgres schema + insertion
6. Postgres query examples for the generator

---

## Edge Cases & Risks
- **All-caps shouting**: Won't accidentally split on dialogue caps (matching against fixed title list is safe)
- **Ambiguous contractions**: LLM pass; if word diff rejects, store original
- **Scene boundaries**: Regex heuristic; may miss "off-page" scenes. Tag conservatively.
- **Unicode apostrophes**: Already handled in chapter title matching; preserve in text

---

## Success Criteria
- All 180 extractable chapters split cleanly
- < 5% of windows flagged for manual review
- Word diff catches any hallucination before storage
- Generator can query scenes by character + location
