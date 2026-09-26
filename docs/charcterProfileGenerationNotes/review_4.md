:01). The reset rules, Phase 2/3, the brief and the config (lines 127–150, 358–419, 459–475) are still the same as in the 10:13 commit.

Where this file contradicts decisions you've already made (v3 has them right)
  - Reset (127–150): the top-N original nodes stay fully active, and dormant ones can be "relearned". You chose lossy fragments instead ("if the character remember exactly what happened they will retake the same steps", review_2.md:54).
  Lossy fragmetns are correct
  - Backstory: only characters.core survives a reset (296–299), so Harry's generated backstory goes dormant in week 2. You said backstory from before the story starts is permanent.
  The backstory generated for the character should persist and be known to the character every week, only the learned knowldede during the week will be reset, then the new fragments of memory will be added to he backstory in the next week
  - Drift: profile_diff, ±0.15 trait drift, weekly profile versions and objective progress carried across weeks (367–387, 470). You confirmed the baseline resets fully every week (review_3.md:5–6).
  correct, its a timeloop, any progress will be reset as well, what is profile_diff and trait drift?

  - Brief (402–405): lists the names of dormant nodes. You said the character sees nothing until a fragment is triggered.
  Correct the character  will forget all knowledge nodes gained during the  week, then during the maintenance at the weekly restart, the new fragments will be added with their profix sequence so  m   thatr will be unlocked witheir intro sequence
  
  - Phase 2 is still one LLM call over the whole week. You chose daily summaries.
Do daily summaries, then the weekly maintenaace will analyze the summaries

  - Week (167–173): a counter the updater increments, stored nowhere. You defined it as a clock window starting Sunday 00:00 New York time. Nothing stops "--once --character harry" from running twice and resetting twice. v2's loop_weeks.reset_steps fixes this.
  When we go live with the ashiorid campaign we will have it timed so the story ends at sunday 00:00, 


  - Experiences are copied from the messages table at reset time. You asked for a real-time Kafka→Postgres consumer.
    yeah we can do real time with a process

  - The knowledge stage (max_nodes: 40) is still in the generator. You moved the graph to its own process.
seperte process is correct

  - Line 389 calls the reset replayable. You agreed every week plays out differently.
  different plays are coorrect



D2 breaks the time loop
  D2 says the weekly worker restart makes a boot-only brief good enough (line 522). Nothing in the repo restarts workers on a schedule; redeploy.sh only runs when you deploy. A worker that stays up past Sunday 00:00 keeps last week's brief, so that character never resets. The Sunday reset has to restart workers or push a refresh, and that needs to be in v1, not deferred.

we will need a way to schedule a process to do the resets, like mentioned propveiusly we will have some kind of scheduler setup for all processes, the reset, maiunetnacen, start, end, initialize, and other will all need ot be scriptable from a scheduler




Decisions I need from you
  - Where the character data lives. This file says generator-postgres (DB "generation", port 5455), which matches your v2 answer "we run a local postgres just for this project". v3 §4 says a new "characters" database on mafober. Points to weigh:
  
  postgres is where it needs to live in a new characterProfile db on the existing potgres, it sohuld have its own user


    - generator-postgres has no backups yet (docker-compose.yml says mirroring to mafober comes later). Fragments are LLM output that can't be regenerated and they build up for months, so set up backups before the pilot wherever the data lives.
  we need a db backup process for the scheduler


    - It runs postgres:16-alpine, which doesn't include pgvector. Either build pgvector into it, or move to pgvector/pgvector:pg16 with a dump/restore. Don't reuse the alpine data volume under a Debian image, because text sorting rules (collations) differ.
    Can you create a new docker hosted pgvector on the stack and use that as a local stack db for testing




    - v3 contradicts itself on pgvector: §0 says it's installed, §4 and O1 say to check.
  needs to be deployed, create a docker compose for me to deploy to 192.168.1.20


  - Pilot cast: harry/ron/hermione from hptest (this file) or v3's top 6?
  start with harry ron hermione

  - D5 direction: with a clock-driven week, the character reset should drive the campaign loop, not the other way round. CampaignRuntime.reset() also has no callers outside the tests.
  at the end of a programmed story it should gracefully end, otherwise we need a stop script. we will scheduyle the character reset and update seperately to the story







Wrong against the code, whichever plan you keep
  - narration, dialogue, campaign_event and chat (lines 190, 474) aren't message types on the bus. What actually gets published is the dev-team types (task_assignment, commit_notification, bug_report, ...), agent_thinking, status_update and replay_*. Nothing in app/campaign/ publishes to the bus, so the experience capture would find nothing. v3's character_say and scene_event messages fix this.
  use v3
  - Reading experiences from the messages table ties memory to the log filter. message-logger drops any type an operator excludes (services/message-logger/logger.py:183), so muting agent_thinking to cut log noise would silently erase experiences. Consuming Kafka directly avoids this.
we will write this data to postgres and display on the tubers screens, lets clear this at 00:00 every night we will need a sscript to schedule the daily maintenance and this be a part of that

  - The messages table has no character column and no timestamp index; the only indexes are on "to" and type (docs/sql/02_create_tables.sql:22–23).

Add those new columns

  - No component owns the capture step. Line 181 says it runs at reset time, line 353 says it's a separate earlier step, and it isn't in the module layout or the build order. Line 56 says the updater reads messages; line 354 says it never does.
waht capture is this? if its soemthing to postgres we will need to setit up 



  - DOUBLE (lines 103, 120) still isn't a Postgres type, so CREATE TABLE fails. It was agreed in review 1 but never changed to DOUBLE PRECISION.
Change it as part of this plan


  - Avatar:
    - character_schema.SLIDERS doesn't exist; the real names are SLIDER_DEFAULTS and resolve_params.
    make a todo that we need to set this up
    - Workers read avatar.codec_avatar.character_params (config/workers/coder.yaml:106), not termgl_avatar (line 410).
    they should all be with the termgl avatar
    - Roundtable tiles only accept a preset name (config/workers/roundtable.yaml:207–212), so a generated slider dict won't render on a tile.
    this needs to be fixed

  - The renderer has 8 shape sliders plus accent_color. It can't draw hair, eye colour, glasses or a scar, so the refinement pass against the written appearance description chases things it can't render. Limit v1 to shape and accent colour.
  ok limit it to that 

  - The pilot source file is a single 5.9 MB line with apostrophes and quotation marks stripped, so dialogue can't be attributed to speakers from it directly. The plan doesn't mention utilities/source_pipeline/ or chapters_v2/.
Its been split into book_chapter_name.txt files C:\Users\matt\PycharmProjects\virtualTubers\sourceworks\chapters_deterministic




Smaller issues
  - Line 413 says "speak in 2nd person"; the cast prompts use first person.
  First person is correct for the cast
  - Some example node names would teach the LLM bad examples: godsley-shelter-abuse, wingardsium-levia, knows-qui-llusions, and pass-third-year-exams for an 11-year-old.
  ok sure, setup some negative and postive examples

  - Build step 4 calls updater.py "the standalone process", but the layout makes it pure logic and puts the process in services/character-updater/.

it should be its own process, or at least have a seperate scriptable execution call

  - Line 53: 3layer-generator connects over 127.0.0.1:5455 with host networking, not POSTGRES_HOST: generator-postgres.

Lets launch a local postgres-alpine as well as the vector version and use those, then we can setupbackups to mafober on https://192.168.1.120 later


  - Still open from review 1: a revert command, indexes on the node/edge tables, and the unclear config/character.yaml vs worker.yaml wording.

Yeah lets have a way to undo for testing

Worth carrying into v3
  - D1's split: the reset logic in app/character/updater.py, the process in services/character-updater/, with "--once --character X --dry-run" as what a scheduler calls. v2 and v3 only have "cli.py reset".
yeah dryrun is ok

  - D3's standalone character_jobs and character_artifacts tables.
They can be deployed on the local postgres

Separate from this plan: .claude/prompts/INDEX.md's next action is a multi-day Ollama Phase 2 run on chapters_deterministic/, which v3 marks as broken. I checked: the source text says "rather ill but happy", and chapters_deterministic/1_001 and 1_003 say "rather I'll but happy". chapters_v2/ has all 199 chapters; INDEX still says 174.
Its stil in progress


Suggested next step: make v3 the main plan and move it to .claude/prompts/, where CLAUDE.md says plans belong. Then add D1, D3 and the fixes above, and mark this file superseded. Before that, confirm two things: which Postgres to use, and that this file is out of date rather than a deliberate return to the v1 model. Once you answer I can write that as v4.











