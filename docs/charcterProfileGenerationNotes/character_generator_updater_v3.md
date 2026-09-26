> Superseded by docs/charcterProfileGenerationNotes/character_generator_updater_v4.md (2026-09-25). Kept as the decision trail.

# Character Generator + Updater — Design Plan v3

Status: design. Stage 0 of §1 is built. Written 2026-09-23.
Supersedes: `character_generator_updater_v2.md`. v2 is kept unchanged as the
decision trail, with the user's inline answers to O1–O5 and the discussion that
followed.

## 0. What changed from v2

| Topic | v2 | v3 |
|---|---|---|
| Scope | Generator for one campaign | A **repeatable, source-agnostic pipeline** (§1). HP is the calibration corpus; Ashiorid and future works reuse it. |
| DB | Undecided | New `characters` database on **mafober**. pgvector is installed on the host (§4). |
| Kafka | Unverified | One topic `vtuber.messages`. Timestamps come from the message body. Dedupe on the message `id`. Retention is probably 7 days (§3). |
| Thoughts | Assumed absent | Already on the bus as `agent_thinking` (`app/agent_metrics.py:259`). This becomes the recall input (§7). |
| Dialogue | Assumed | No character-dialogue message type exists. Add a `character_say` contract (§3). |
| Recall | Cue index + vector top-K on the scene | **Beat-trajectory matching**: embed each thought or line, then align recent beats against each fragment's lead-up (§7). |
| Embeddings | Undecided | Any OpenAI-compatible `/v1/embeddings` endpoint. Ollama now, vLLM on gx10 once it's set up (§7). |
| Start point | Undecided | **Book 1, chapter 1.** Baseline = each character's state before chapter 1. Backstory has two layers (§5). |
| Test cast | Undecided | Top 6 by dialogue: Harry, Dumbledore, Hermione, Ron, Hagrid, Snape (§1 stage 3). |
| Roundtable | Fixed cast | Per-show recasting via the show header. Characters can be absent without any reset (§6). |

## 1. Source pipeline (repeatable, per source work)

Code: `utilities/source_pipeline/` (README there). One YAML per source under
`sources/`; no per-source Python. Every stage is idempotent and resumable, and
never edits the original text. Every output keeps offsets back into the
original so any claim can be traced to its source passage.

| # | Stage | In | Out | Validation |
|---|---|---|---|---|
| 0 | **Split** (built) | flat text + table of contents + `heading_aliases` | chapters + `manifest.json` | all headings found in order; words per chapter within bounds |
| 1 | **Clean** | chapters | cleaned chapters | word-sequence diff against the original after stripping punctuation; any window that differs is rejected |
| 2 | **Tag** | cleaned chapters | scenes: `{chapter, offsets, speakers[], present[], location}` | speaker of each quoted line; checked against the calibration answer key |
| 3 | **Cast** | tags | ranked cast, alias sets, `cast_size` N | HP: top 6 must match the answer key below |
| 4 | **Timeline** | tags + table-of-contents dates | events with a `story_pos` (book, chapter, offset); a `pre_story` era for everything before chapter 1 | events are ordered consistently with the table-of-contents dates |
| 5 | **Backstory** | timeline + passages | two layers per character (§5) | every claim cites evidence offsets |
| 6 | **Baseline** | backstory + passages | `character_baselines` row (v2 §4 profile shape) | JSON schema; claims cite evidence |
| 7 | **Export** | DB | cast YAML | same validator as today's cast files |

Per-source config (`sources/<id>.yaml`): text path, table of contents and
format, heading aliases, campaign start `story_pos`, `cast_size`, alias seeds,
prompt profile names. Ashiorid replaces stages 0–4 with an adapter over its
authored vault (`utilities/3LayersWeeklyGeneration/src/source_adapter.py`
already reads it). Stages 5–7 are shared.

**Calibration answer key (HP only).**
`sourceworks/harry_potter_characterWordCounts/harry_potter_characters_with_dialogues.csv`
is third-party **film** dialogue (MIT, L. Chauvet), not the books. It isn't a
pipeline input. It is a check: stage 3's own ranking from the books must put
the same 6 characters at the top:

| Rank | Character | Film words |
|---|---|---|
| 1 | Harry Potter | 13,970 |
| 2 | Albus Dumbledore | 7,653 |
| 3 | Hermione Granger | 7,634 |
| 4 | Ron Weasley | 6,916 |
| 5 | Rubeus Hagrid | 3,473 |
| 6 | Severus Snape | 2,381 |

A new source has no such key. The pipeline has to rank the cast without one,
which is why it is calibrated on HP first.

### Stage 0 result (HP)

199/199 chapters, 1,087,064 words, 1,630–8,954 words per chapter, in
`sourceworks/chapters_v2/`. The old `split_chapters.py` output
(`sourceworks/chapters/`) has three defects and must not be used:

- It dropped 14 chapters.
- In 26 files, a chapter ran to end of file (`1_001` held all 1.09M words).
- Five chapters started early, at an in-text mention of their title.

`chapters_deterministic/` and `chapters_llm_restored/` were built on that
output and must be regenerated from `chapters_v2/`.

### Stage 1 defect to fix before re-running

`sourceworks/phase1_deterministic.py` treats these contractions as
unambiguous, but each is also a common word:

| Restored as | Ordinary word | Count in the corpus |
|---|---|---|
| we'll | well | 2,260 |
| I'll | ill | 635 |
| I'd | id | 379 |
| let's | lets | 249 |
| he'll | hell | 152 |
| we'd | wed | 145 |
| she'd | shed | 83 |
| she'll | shell | 72 |

It already corrupts text. For example, "looking tired and rather ill but happy"
became "rather I'll but happy". Move these to the LLM pass, where stage 1's
word-diff guard allows apostrophe-only changes. Keep only the truly
unambiguous forms (didnt, wont, cant, im, ive, …) deterministic.

## 2. The model in one paragraph

Unchanged from v2 §1. It's a weekly loop. Each character has a permanent
baseline. Days are summarised at 00:00 NY. On Sunday 00:00 NY the week's
knowledge is archived, and characters with `retains_fragments` gain N lossy
memory fragments that surface only through recall.

## 3. Bus and ingestion

Verified in `docker-compose.yml` and `app/message_bus.py`:

- **Topic**: a single `vtuber.messages` carries everything, including
  heartbeat and status floods. Ingest uses an **allowlist** of types.
- **Timestamp**: `build_message()` stamps a UTC ISO `timestamp` in the message
  body. Use it for `loop_day` and `loop_week`, not Kafka's CreateTime.
- **Dedupe key**: the message `id` (UUID). The `(topic, partition, offset)`
  key from v2 is dropped. `message-logger` already dedupes on `id`.
- **Consumer**: don't reuse `MessageConsumer`. It hardcodes
  `auto_offset_reset="latest"` and `enable_auto_commit=True`. Ingest needs
  `earliest` plus a manual commit after the DB insert.
- **Retention**: the bundled broker leaves the default of 168h, which is one
  loop week and leaves no margin. Check the external broker with
  `kafka-configs --describe --entity-type topics --entity-name vtuber.messages`.
  Set it to at least 14 days. If ingest falls behind anyway, `messages`
  (written by message-logger) is a backfill source.

**Types ingested as experience** (`experience_events.msg_type`):

| Type | Exists? | Attributed to | Visibility |
|---|---|---|---|
| `agent_thinking` | yes (`agent_metrics.py:259`) | `from` | **only its author**; never another character's context or recall |
| `character_say` | **new** | speaker and every addressee/listener in the scene | everyone present |
| `scene_event` | **new** (GM/narrator) | everyone present | everyone present |

New contract, published by the character worker and the GM:

```json
{"type": "character_say", "from": "<agent_id>", "to": "broadcast",
 "payload": {"scene_id": "...", "speaker": "<character slug>", "addressees": ["..."],
             "present": ["..."], "text": "...", "story_pos": {"book": 1, "chapter": 1}}}
```

`present[]` is what makes shared history per-character: a character only
experiences scenes they are present in.

## 4. Database: `characters` on mafober

- New database `characters` on the mafober Postgres instance, which is outside
  compose.
- Ownership uses two roles inside it: `character_ingest` owns
  `experience_events` and `character_agents`, and `character_service` owns
  everything else. They share one DB because experience rows need foreign keys
  to characters, and Postgres can't join or enforce foreign keys across
  databases.
- pgvector setup on mafober, as the host admin:
  ```sql
  SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name='vector';
  -- if absent: apt install postgresql-<major>-pgvector, then in the characters DB:
  CREATE EXTENSION vector;
  ```
  Until then, recall runs cosine similarity in-process. That's fine at this
  scale (§7).
- Tables: v2 §4, plus:
  - **source_works**: `id`, `title`, `config_sha256`, `loaded_at`.
  - **source_chapters**: `source_id`, `number`, `book`, `title`, `story_date`,
    `body_offset_start/end`, `original_text`, `cleaned_text`, `sha256`.
  - **source_scenes**: `source_id`, `chapter`, `offset_start/end`,
    `speakers TEXT[]`, `present TEXT[]`, `location`. GIN index on `present`.
  - **timeline_events**: `id`, `source_id`, `story_pos` (book, chapter,
    offset), `era` (`pre_story|story`), `summary`, `participants TEXT[]`,
    `evidence` (offsets), `revealed_at_pos`. The last one is the point where a
    reader or character learns of the event, used for §5 filtering.
  - **character_backstories**: `character_id`, `layer` (`believed|truth`),
    `content JSONB`, `evidence`, `version`.
  - `character_baselines` gains `start_pos` (the story position the baseline
    represents).
- Embedding columns carry `embed_model` and `embed_dim`. Changing models
  requires re-embedding, and each HNSW index is fixed to one dimension.

## 5. Baselines at book 1, chapter 1 — two-layer backstory

The campaign opens at book 1, chapter 1 (November 1981). A character's
baseline is their state **before chapter 1**. Much of that history is only
revealed in later books, so each character gets two layers:

| Layer | Who sees it | HP example: Harry | HP example: Snape |
|---|---|---|---|
| `believed` | the character | infant; parents unknown to him | double agent, grieving Lily, sworn to protect her son |
| `truth` | GM only | Godric's Hollow, Lily's protection, the scar's link to Voldemort | same, plus how Dumbledore uses him |

- Stage 5 reads the **whole series** for `pre_story` events but writes a
  character's `believed` layer only from what that character knows at
  `start_pos`.
- An event's `revealed_at_pos` decides when it can move into a character's
  knowledge. This is optional; loop weeks are free play, not a replay of the
  books.
- Harry at 1981 is an infant. For a playable Harry, the campaign's
  `start_pos` can be set to chapter 2 (June 1991) while keeping the chapter 1
  night as a cold open played by Dumbledore, Hagrid, and McGonagall as a
  guest. `start_pos` is a per-campaign config value, so this is a setting,
  not a redesign.
- Ashiorid's authored backstories go straight into the `truth` and
  `believed` layers, skipping stages 0–4.

## 6. Roundtable casting

Verified: slot is separate from persona (`config/workers/roundtable.yaml:34`).
Each show header's `show.slots` and `show.cast` (`docs/voice_gate.md:92`)
assign characters to tiles for that show.

- Recasting between shows is a new header. Tiles are reused, and nothing
  restarts.
- A character's memory lives in the DB, keyed by character, not slot.
  Changing Harry's slot or leaving him out of a show doesn't touch his
  baseline, knowledge, or fragments. **The only reset boundary is the week.**
- Layout for 8 tiles: GM on `tuber_0`, the 6 mains on fixed tiles (so the
  audience learns where each sits), and 1 guest tile recast per show.
- A silent slot doesn't stall a show. Tested:
  `test_follower_with_no_owned_scenes_still_sends_ready`. A follower whose
  cached rows don't match the episode script *does* stall, by design (the
  director times out; `test_follower_speaker_not_in_script_never_sends_ready`).
- Each character keeps their own stream showing their workspace and thoughts.
  The roundtable is the visual form of the shared topic.

## 7. Fragment recall: beat-trajectory matching

A **beat** is one `agent_thinking` from the character, or one `character_say`
or `scene_event` the character is present for. Each beat is embedded once when
it's ingested.

**A fragment stores:**

- `gist`: lossy, emotional, first person
- `hooks`: entities, places, objects, the emotional tone, and the loop weekday
  and hour
- `lead_up`: the embeddings of the 6–10 beats before the moment (the
  "a b c … j" before "k" in the user's framing)
- `gist_embedding`

**Per beat, for characters with `retains_fragments`:**

1. **Hooks** (exact, cheap). Look up entity and place aliases in the beat
   against the hook index. A match adds activation.
2. **Trajectory**. Align the character's last W beats against each fragment's
   `lead_up` with a fuzzy subsequence alignment (Smith-Waterman on cosine
   scores). A partial ordered match counts, so 3 of 8 lead-up beats in order
   adds some activation.
3. **Gist**. Cosine similarity of the beat to `gist_embedding`.
4. **Activation** per fragment = a weighted sum of 1–3, decaying over time.
   `fragment_links` spread part of it to neighbours (chains).
5. Thresholds:
   - `unease` (faint): inject "something feels familiar" with no content.
   - `surface`: call the LLM judge with the scene and the gist. On yes, inject
     the gist and log it to `fragment_recalls`.
   - A cooldown applies per fragment.

The gradual build is deliberate. Viewers who watched last week can see the
approach before the character notices.

**Cost.**

- About 52 fragments per character per year × 8 lead-up beats ≈ 400 vectors.
- 8 tubers at 1 beat per 5 s ≈ 1,600 dot products a second, which is
  negligible.
- The real per-beat cost is one embedding call. The LLM runs only at
  `surface`.

**Why not token IDs or hidden states.** vLLM can return token IDs with the
output (`return_token_ids`), and since 0.18 it can extract hidden states
through a KV connector that writes safetensors files. Neither suits per-turn
matching:

- Token IDs are arbitrary vocabulary indices. "think" and "thinking" aren't
  numerically close.
- Hidden-state extraction covers prompt tokens only, writes about 268 MB per
  8k-token request, and blocks on those writes.
- Hidden states are specific to the prompt and the model, so a model swap
  would orphan every fragment.

A dedicated embedding model gives stable, comparable vectors.

**Embeddings.**

- Use `recall.embeddings: {base_url, model, dim}` against any
  OpenAI-compatible `/v1/embeddings` endpoint.
- Start on Ollama (`nomic-embed-text` 768-d or `bge-m3` 1024-d). Switch to
  vLLM on gx10 by config once it's up, served next to the chat model.

**Harness first** (unchanged from v2). Replay stored beats against hand-made
fragments, report precision and recall for each signal and for the combined
activation, and tune weights and thresholds before wiring it into workers.

## 8. Config additions (`config/character.yaml`)

```yaml
character:
  db: { host: mafober, dbname: characters }
  campaign: { source: harry_potter, start_pos: { book: 1, chapter: 1 }, cast_size: 6 }
  ingest:
    kafka_topic: vtuber.messages
    types: [agent_thinking, character_say, scene_event]
    dedupe_key: id
  recall:
    embeddings: { base_url: http://localhost:11434/v1, model: nomic-embed-text, dim: 768 }
    lead_up_beats: 8
    window_beats: 12
    weights: { hooks: 0.3, trajectory: 0.5, gist: 0.2 }
    thresholds: { unease: 0.45, surface: 0.7 }
    cooldown_beats: 40
    judge_profile: character_recall_judge
```

Everything else is as in v2 §8.

## 9. Open items

- **O1.** Confirm pgvector on mafober. Run the SQL in §4.
- **O2.** Check Kafka retention on the external broker. Raise it to ≥14 days.
- **O3.** vLLM on gx10: chat model plus an embedding model, set up after the
  plan is final. Until then, embeddings come from Ollama.
- **O4.** Harry at `start_pos` 1.1 is an infant. Keep the chapter 1 cold open
  with a chapter 2 `start_pos`, or run chapter 1 with Harry offstage?
- **O5.** Stage 2 speaker attribution: rule-based ("… said Harry") first,
  with the LLM only for unattributed lines? Measure accuracy on HP book 1
  before scaling.

## 10. Build order

0. **Source pipeline stage 0** — done (`utilities/source_pipeline/`, 18 tests).
1. Fix the phase 1 contraction list (§1). Re-run stage 1 cleaning on
   `chapters_v2/`.
2. Create the `characters` DB on mafober, roles, and pgvector. Write the
   schema and `store.py` with tests.
3. Pipeline stages 2–3 (tag, cast) and check them against the HP answer key.
4. Stages 4–6 (timeline, two-layer backstory, baseline) for the 6-character
   test cast. Stage 7 export.
5. Bus: the `character_say` / `scene_event` contracts and the ingest consumer,
   using the allowlist and `id` dedupe.
6. Daily summariser and weekly reset (v2 §6, unchanged).
7. Recall harness (§7): beat embedding, trajectory alignment, and tuning on
   replayed beats.
8. Wire recall into workers, plus brief assembly.
9. Pilot: Harry with `retains_fragments`, 2–3 real weeks.
