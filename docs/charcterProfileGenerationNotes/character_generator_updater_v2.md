> Superseded by docs/charcterProfileGenerationNotes/character_generator_updater_v4.md (2026-09-25). Kept as the decision trail.

# Character Generator + Updater — Design Plan v2

Status: design, not yet implemented. Written 2026-09-23.
Supersedes: `docs/character_generator_updater_handoff/character_generator_updater.md` (v1).
Decision trail: `docs/character_generator_updater_handoff/character_generator_updater_review.md`,
`review_2.md`, `review_3.md` (user answers inline).

Note: the code paths cited in v1/review 1 (`app/`, `services/`, commit `1497bd9`)
live in the main VTuber repo, not this checkout. Re-verify them there before building.

## 1. The model in one paragraph

A campaign runs as a weekly time loop. Every character starts each week from a
permanent **baseline** (generated from source material: personality, backstory,
objectives). During the week, characters (live AI agents) talk over Kafka;
everything is captured to Postgres. Each day at 00:00 the day is summarised.
At Sunday 00:00 the week ends: the week's learned knowledge is **archived**
(kept, never deleted, inaccessible to the character), and for characters with
fragments enabled, N **memory fragments** are created: new, lossy, emotional
gists of the week's most significant moments ("felt victorious grabbing his
enemy's face"), not transcripts. Fragments stay hidden until a scene triggers
them (déjà vu), and they build up across weeks. Week N = baseline + fragments
from weeks 1..N-1. Lossy is deliberate: exact recall would make characters
repeat the same steps. Every week plays out differently.

## 2. Decisions (all confirmed by user)

| # | Decision |
|---|---|
| Profile | Baseline resets fully each week: personality, profile, objectives. No weekly drift. Only fragments carry forward. |
| Loop awareness | Characters don't know they're in a loop. Updating objectives once they have gathered enough fragments is **future work**, left as a seam (§9). |
| Fragments | Immutable and permanent (no strength or decay). New knowledge can link to a fragment it grew from. That knowledge can later become a fragment itself (chain). |
| Who keeps fragments | Per-character config flag `retains_fragments`, default `false`, `true` for the main streamed character. Everyone else resets fully. |
| Fragments per week | Configurable. Start with **1**. |
| Week boundary | Sunday 00:00, timezone `America/New_York` (explicit in config). |
| Day boundary | Real time, 00:00 `America/New_York`. |
| Knowledge graph | Built by a separate process. The generator only produces the profile data and the data contract the graph process reads. |
| Source of truth | Postgres. Source material is loaded into the DB, the generator reads it from there, and the cast YAML is generated from the DB. |
| Ingestion | A real-time Kafka→Postgres consumer. |
| Trigger | Two steps: a cheap retrieval step picks candidates, then an LLM judge confirms. Build it as a test harness first (§7). |
| Divergence | Characters' memories of shared history may diverge (accepted). |
| Replayability | Only the database apply step is predictable. LLM outputs are stored for audit, not replay. |
| Misc | `DOUBLE PRECISION`, indexes on every hot lookup, `config/character.yaml` (character replaces worker), a `revert` command built now for later use. |

## 3. Components

```
kafka topic ──► ingest consumer ──► experience_events (Postgres)
                                          │
                      00:00 daily ──► daily summariser ──► daily_summaries
                                          │
                 Sunday 00:00 ──► weekly reset ──► memory_fragments + archive
                                          │
 source_documents ──► generator ──► characters / character_baselines ──► cast YAML
                                          │
          worker turn ──► fragment recall (retrieve → judge) ──► fragment_recalls
                                          │
                     (separate process) knowledge graph builder
```

1. **Source loader**: loads campaign source material into `source_documents`
   (content and sha256). The format of the user's campaign is TBD, so chunking is
   designed once the material is examined.
2. **Generator**: stage registry (keep the v1 `Stage` protocol). Stages:
   `profile`, `avatar`, `brief`, `cast_export`. The `knowledge` stage moves out
   to the graph process. The generator emits the node and edge data contract
   that process consumes.
3. **Ingest consumer**: a long-running Kafka consumer, one consumer group,
   manual offset commit **after** the Postgres insert (at-least-once delivery),
   idempotent insert keyed on `(topic, partition, offset)`. It maps agent/worker
   id → character via `character_agents`. It derives `loop_day` and `loop_week`
   from the message timestamp in the configured timezone. It must keep up with
   Kafka retention, so alert on consumer lag.
4. **Daily summariser**: at 00:00 NY, for each character, summarise the previous
   day's `experience_events` into a `daily_summaries` row. The per-day LLM
   call is bounded, validated against a strict JSON schema, and retried a
   limited number of times.
5. **Weekly reset**: at Sunday 00:00 NY, run the steps in §6.
6. **Fragment recall**: runtime, per worker turn (§7).
7. **Knowledge graph builder**: separate process, out of scope here. It reads
   the baseline, the week's knowledge, and fragments, and owns the graph views.

## 4. Data model (Postgres)

All IDs are TEXT UUIDs. Every table gets `created_at TIMESTAMPTZ DEFAULT now()`.
Mirror in `docs/sql/02_create_tables.sql` and `docs/database_schema.md`. Each
table needs a named owner (see §10).

**source_documents**: `id`, `campaign`, `title`, `content TEXT`, `sha256`, `loaded_at`.

**characters**: `id`, `name UNIQUE`, `slug UNIQUE`, `campaign`, `status`
(`draft|active|retired`), `retains_fragments BOOLEAN DEFAULT FALSE`,
`is_main BOOLEAN DEFAULT FALSE`, `avatar_params JSONB` (codec_avatar input;
how appearance maps to params is TBD), `updated_at`.

**character_agents**: `agent_id PK`, `character_id FK`. This maps bus IDs to characters.

**character_baselines** (versioned; never modified by the loop): `character_id`,
`version`, `profile JSONB` (v1 profile shape: identity, personality,
self_image, hopes, desires, objectives, secrets, quirks, backstory_summary),
`backstory_nodes JSONB` (the permanent knowledge contract for the graph process),
`source_document_ids TEXT[]`, `change_notes`, `created_by`. PK is `(character_id, version)`.
`characters.active_baseline_version` points at the one in use. `revert` = move the pointer.

**loop_weeks**: `week INT PK`, `campaign`, `starts_at`, `ends_at`,
`status` (`open|closing|closed`), `reset_steps JSONB` (step → completed_at, see §6).

**experience_events**: `id`, `topic`, `partition`, `kafka_offset`,
`UNIQUE(topic, partition, kafka_offset)`, `ts TIMESTAMPTZ`, `from_agent`,
`to_agent`, `character_id` (nullable if unmapped), `msg_type`, `payload JSONB`,
`loop_week`, `loop_day DATE`.
Index: `(character_id, loop_week, loop_day)`, `(ts)`.

**daily_summaries**: `character_id`, `loop_day DATE`, `loop_week`,
`summary JSONB` (events, significant moments with salience and evidence event ids,
new knowledge items), `llm_model`, `raw_response`. PK is `(character_id, loop_day)`.

**week_knowledge_nodes / week_knowledge_edges**: knowledge learned in a week.
Same shape as v1 nodes and edges, plus `loop_week`, `archived_at_week INT NULL`
(replaces `active`), and `grew_from_fragment_id NULL` on nodes (chain link).
Index: `(character_id, archived_at_week)`, `(character_id, loop_week)`.
Nothing is deleted. The character can only reach rows where `archived_at_week IS NULL`.

**memory_fragments** (immutable once written): `id`, `character_id`,
`source_week`, `gist TEXT` (lossy, emotional or sensory, first person),
`trigger_cues JSONB` (entities, places, situations, emotional tone),
`embedding` (vector, see §7), `source_node_ids TEXT[]`,
`source_event_ids TEXT[]`, `parent_fragment_ids TEXT[]`, `rank_rationale`.
There are no mutable columns. Enforce by only ever inserting, plus a test.
Index: `(character_id)`, a vector index on `embedding`, and a GIN index on `trigger_cues`.

**fragment_links**: `from_fragment_id`, `to_fragment_id`, `relation`
(`grew_from|co_occurred|evokes`). This supports chain recall.

**fragment_recalls**: `id`, `fragment_id`, `character_id`, `loop_week`, `ts`,
`scene_excerpt`, `retrieval_score`, `judge_verdict`, `judge_reason`. This is the
audit record and also the signal for the future loop-awareness logic.

**character_jobs / character_artifacts**: standalone job tables, keeping the
3layer store untouched (v1 D3 stands).

## 5. What a character sees (the brief)

Assembled at worker boot from the DB:
1. Baseline profile (active version): identity, personality, speech style,
   objectives, locked core.
2. Baseline knowledge, plus the current week's knowledge learned so far.
3. **No fragment list.** Fragments enter the context only when recall fires (§7).
4. Behaviour contract: act as the character, and treat a recalled fragment as a
   déjà vu feeling or instinct, never as a known fact about a loop (until loop
   awareness exists).

When a fragment is recalled mid-scene, it is injected into the next turn's
context as a short "a feeling surfaces: <gist>" block. It stays available for the
rest of the week.

## 6. Weekly reset (Sunday 00:00 America/New_York)

Order is fixed. Each step records its completion in `loop_weeks.reset_steps`,
and a re-run skips steps already done.

1. **Close Saturday**: run the daily summariser for the final day.
2. **Select fragments**: only for characters with `retains_fragments`. The LLM
   ranks significant moments across the 7 `daily_summaries` rows, picks
   `fragments_per_week` (config, default 1), and writes each as a new lossy
   `memory_fragments` row with trigger cues. Candidates can include nodes that
   `grew_from_fragment_id`, which creates `fragment_links` rows (chain).
   Store the prompt model and raw response.
3. **Archive**: set `archived_at_week = W` on all of that week's knowledge
   nodes and edges, for **every** character. Run it as one transaction.
4. **Open week W+1**: insert the `loop_weeks` row (next Sunday → Sunday),
   status `open`.

`--dry-run` prints what each step would do without writing. The reset can be
run manually (`cli.py reset --week W`) as well as by the scheduler.

## 7. Fragment recall: two steps, built as a test harness first

Goal: a cheap check per turn that stays cheap as fragments build up (about 52 per year).

**When to check.** Not on every token-level turn. Check on scene events: a
new scene or location, a new entity entering, or a significant beat flagged by
the narrator/GM. Add a cooldown per fragment. This cuts the call volume the most.

**Step 1: retrieve (no LLM, milliseconds).** Combine:
- **Cue index**: entities and places in the scene matched against
  `trigger_cues` via the GIN index. Exact and cheap.
- **Vector search**: embed the scene excerpt and query the top K nearest
  fragments via a vector index (pgvector HNSW if available, see open item O1).
  This stays fast as the table grows.
- Merge, dedupe, and keep candidates above a score threshold, at most K (config, e.g. 3).
  If no candidates pass, stop here. That's the common case and costs nothing.

**Step 2: judge (LLM, only when step 1 returns candidates).** Given the scene
and the candidate gists, does this situation genuinely echo one? Returns a
strict JSON verdict. On yes, inject the fragment and log it in `fragment_recalls`.

**Chains.** After a recall, the fragment's `fragment_links` neighbours become
retrieval candidates on the next check with a score boost. That is how a chain
of memories surfaces earlier over several weeks.

**Harness first.** Before wiring into workers: a CLI that replays stored
`experience_events` scenes against a set of fragments and reports
precision and recall of retrieve-only vs retrieve+judge, plus cost per check.
Tune thresholds there. The rest of the system doesn't depend on the final method.

## 8. Config (`config/character.yaml`)

```yaml
character:
  timezone: America/New_York
  loop:
    week_starts: sunday
    day_rollover: "00:00"
    fragments_per_week: 1
  ingest:
    kafka_topic: <topic>
    consumer_group: character-ingest
    lag_alert_seconds: 300
  recall:
    check_on: [scene_change, new_entity, flagged_beat]
    cooldown_turns: 20
    retrieve_top_k: 3
    retrieve_min_score: 0.75
    judge_profile: character_recall_judge
  daily_summary: { profile: character_summary, max_retries: 2 }
  fragment_select: { profile: character_fragment }
  generator:
    stages:
      profile: { profile: character_profile_strong }
      avatar:  { profile: character_avatar }
  characters:            # per-character overrides; retains_fragments defaults false
    <main-character-slug>: { retains_fragments: true, is_main: true }
```

## 9. Future seams (explicitly not built in v1)

- **Loop awareness.** Once a character has enough fragments and recalls
  (signal: `fragment_recalls` count or chain depth passing a configured
  threshold), start updating objectives. The trigger rule and objective-update
  mechanism are TBD. For now it's only a hook point after step 2 of §6.
- **Fragment removal.** Fragments are permanent unless some future mechanic removes them. None is planned.
- **Avatar changes from knowledge or experience.** codec_avatar is the target. The mapping is TBD.
- **Push-refreshing the brief at reset** (recall already handles mid-week injection).

## 10. Open items

- **O1.** Is pgvector installed or installable on the Postgres instance? If not,
  step 1 falls back to cue index + in-process cosine similarity over the character's
  fragments (fine at hundreds of rows).
It should be, we run a local postgres just for this project 


- **O2.** Table ownership. Proposed: the ingest consumer owns `experience_events`
  and `character_agents`. A new `character-service` owns everything else. List
  every schema copy that must stay in sync.

ok lets do that, it can be a sperate db in postgres

- **O3.** Kafka topic name(s), retention, and message timestamp semantics
  (CreateTime vs LogAppendTime). Confirm from broker config.

check the docker file C:\Users\matt\PycharmProjects\virtualTubers\docker-compose.yml


- **O4.** Campaign source material format and size, to drive chunking and the
  generator's evidence pointers. Deferred by user until planning is done.


lets continue with C:\Users\matt\PycharmProjects\virtualTubers\sourceworks\Harry_Potter_all_books_preprocessed.txt


- **O5.** The embedding model for fragments and scenes (local vs API).

We ened to investigate this further, what optinos do you think would give results that would make feel cool to the audience?
It could be anything, a time, place, person, event, just a thought in the right direction could spark an idea path that come  back to that fragment. 
What if we have some kind of sequence of thoughts, the LLMs think in math which is tokenized to words, what if we matched the same kind of generation pattern?
What if the fragment is represented by 'g' and in the string a b c d e f g, g is the 7th token in that sequence, leading up to g are all the other letters, consider that sequence of fragments as well as the series of tokens that are in a thought sequence of the ai model, waht if we calcualted a type of token pattern that was similar enough. 
As i understand it the vector db of the model has a mapping of strings to numerical tokens, if we have an approx value of each token in the string a b c d e f g would be something like "test:a string:b of:c testing:d test:e cases:f end:g" and the string values would be some numberical representation I'm; making some assumptions about the values in this next string, i dont know if they actually work like this, but i'm thinking that if the words are similar like think and thinking the values would be similar, if this is true we can have some sequence of words that trigger the fragment,

"test:a:2363.2353 string:b:3436.0929 of:c:8532.6829 testing:d:2363.2420 test:e:2363.2353 cases:f:5465.6843 end:g:9803.1587". 

now consider having a long sequence  a b c d e f g h i j k l m n.   Lets say that 'k' is the the the start of the memory fragment, and that continues on starting at k l m n o p q r s t, ad it ends at r. which gives us a lead up sequence, a b c d e f g h i j, the fragment k l m n o p q r, then the lead out sequence s t u v w x ......
If we have a way look at the tokenization from the model i think it owuld be effecient, we would ge the model output and the tokenizd values in one shot and we wouldnt need to do the tokenization, If thats not possible we would need to tokenize the thinking words, then we would have to store all the fragments and check them against each sequence of words, i've seen compiuter scneice questions about this, we need to match patterns effeciently.it could be an o(1) lookp on a hash map, but then we would still have multiple tokens per second in addition to whats already running on the stack, its going to be a neverending list of tokens from 'n' tubers against 'p' memory fragment lists where 'p' grows by 'm' fragments per week per 'n' tuber. then we also need to consider that 'p' has 't' elements (tes string of testing test cases end) . We would need to constantly match the sequence of tokens coming from the agent, or near values (but that might require more calculations if we wanted to consider that 'a:test' and 'd:testing' are similar values, and we could do some math saying that if token a from the agent is similar to d in the sequence we could consider something like with  5% difference in value the word is close enogh to match), but this might be more than o(1), and also we could have bad matches where tesla:w:2363.2319 it would also be within 5% and would be matched, and we could end up matching a bad sequence. ANd we would need to match all of the words in the lead up sequence to the tokens coming from the agent. 

What do you think of this plan? do you have questions or thoughts?




## 11. Build order

1. Schema + `store.py` + validators + tests (no LLM).
2. Ingest consumer (Kafka→Postgres, idempotent, day and week tagging) + tests with a fake consumer.
3. Source loader + generator `profile` stage + baseline versioning + cast YAML export + `revert`.
4. Daily summariser (fake LLM in tests).
5. Weekly reset steps 1–4, including resuming after a crash and `--dry-run`.
6. **Recall harness** (§7). Tune on replayed data.
7. Wire recall into workers + brief assembly.
8. Scheduler (daily and weekly jobs in NY timezone) + docs + CHANGELOG.
9. Pilot: one main character with `retains_fragments`, run 2–3 real weeks, inspect
   fragments and recalls.
