# External-LLM Campaign Pack Authoring Prompt

How to use (operator notes — do NOT paste this header section):
1. Fill in the CAMPAIGN BRIEF block at the bottom of the prompt (premise, tone,
   cast ideas, genre verbs). Leave fields blank to let the model invent them.
2. Paste everything from "BEGIN PROMPT" to "END PROMPT" into Grok (or any
   long-context model). If the reply is cut off, say "continue from the last
   complete === FILE block" and append the continuation to the same file.
3. Save the model's whole reply to a text file, then unpack + validate:
       .venv/bin/python .claude/prompts/unpack_llm_pack.py reply.txt            # dry run
       .venv/bin/python .claude/prompts/unpack_llm_pack.py reply.txt --write    # writes campaigns/<name>/
4. Paste any errors the script prints back to the model ("fix these errors,
   re-emit only the changed files") and repeat until clean.
5. Import to the generator Postgres and run a scoped generator config (see the
   virtualtubers-campaign-content skill). generation_hints.yaml is a starting
   point for that config's `state:` and `arc.day_phases:` blocks.

Source of truth for the contract below: app/campaign/pack.py (loader),
app/campaign/validator.py (semantic checks), app/campaign/primitives.py
(action verbs), app/emotion.py, config/voices.yaml,
utilities/3LayersWeeklyGeneration/src/{arc_schema,segment_schema}.py.
If any of those change, update this prompt.

---------------------------------------------------------------- BEGIN PROMPT

You are authoring a CAMPAIGN PACK for "virtualTubers": an always-on Twitch
show where up to 8 AI-voiced avatar characters ("tubers") sit at a virtual
roundtable and perform a serialized story, 24 hours a day. Everything you
write is SPOKEN ALOUD by text-to-speech and read by downstream LLMs. Your
output is machine-parsed and validated; a single structural mistake fails the
whole pack, so follow the format exactly.

## 1. What happens to your pack (so you understand why the rules exist)

Your pack is a set of YAML/Markdown files. It is loaded by a strict Python
loader + validator, imported into a database, and then fed to an offline
"3-layer generator" that stretches it into ~168 hours (one week) of airtime:

- Layer 1 (arc planner): reads campaign title, genre, every SPINE scene's
  `id`, `title` and `enter_narration`, every lore note IN FULL, and the list
  of ambient scene ids. It plans the week as ~28 six-hour segments arranged
  as a RING: a descent (things build), a keystone (the central turn), and an
  ascent that mirrors the descent in reverse (callbacks, consequences,
  resolution). Each segment gets a synopsis and a continuity_in/out.
- Layer 2 (segment planner): splits each segment into "slots". A slot is
  either `spine` (plays one of YOUR authored spine scenes, referenced by id)
  or `ambient` (a short improvised filler scene). It may ONLY use your cast
  ids, your lore stems, and your scene ids — anything else is rejected.
- Layer 3 (dialogue): a smaller LLM improvises the ambient slots line by line,
  in character, using each cast member's `archetype` + `system_prompt`, the
  lore notes the slot names, and the slot prompt.

Consequences for you:
- Spine scenes are the only plot. They play once, verbatim, in order. They
  must be complete, polished and good.
- Ambient scenes are the bulk of the airtime. They carry NO plot decisions —
  they are texture: banter, routines, rivalries, recurring jokes, places.
  Write many, varied, each a reusable seed a small model can riff on for
  5-15 minutes without changing the story.
- Lore notes and cast system_prompts are the generator's only memory of your
  world. Anything not written there does not exist downstream. Make them
  dense, concrete and consistent.
- Every name, id and vocabulary value is a CLOSED SET. If you reference a
  lore stem, scene id, cast id, primitive, mood or voice that you did not
  define (or that is not on the allowed lists below), the pack fails.

## 2. Output format (MANDATORY)

Emit every file as a delimited block, nothing between blocks except blank
lines. No markdown code fences inside or around the blocks. No commentary
before the first block or after the last one.

=== FILE: campaign.yaml ===
...file contents...
=== END FILE ===

Paths are relative to the pack root. Required files:
- campaign.yaml
- cast/<cast_id>.yaml          one per cast member (gm + every player)
- scenes/<NN>-<scene-id>.yaml  spine scenes, NN = 00, 01, 02 ... in story order
- scenes/a<NNN>-<scene-id>.yaml ambient scenes, a001, a002 ...
- lore/<stem>.md               lore notes; the filename stem IS the lore id
- generation_hints.yaml        generator tuning hints (section 9)

Emit files in this order: campaign.yaml, cast, lore, spine scenes, ambient
scenes, generation_hints.yaml. Emit generation_hints.yaml LAST; it doubles as
the "I finished" marker.

## 3. Identifiers

- All ids (cast ids, scene ids, lore stems, campaign name) are lowercase.
  Campaign name: snake_case (e.g. `harbor_watch`). Cast ids: snake_case role
  words (e.g. `captain`, `night_clerk`). Scene ids and lore stems:
  kebab-case or snake_case, pick one style and keep it (e.g. `the-first-alarm`).
- A scene file's `id:` field is what matters, not its filename, but keep them
  matching: `scenes/03-the-first-alarm.yaml` has `id: the-first-alarm`.
- Scene ids must be unique across the whole pack.

## 4. campaign.yaml

Keys:
- name (required): snake_case pack id.
- title (required): human title shown on stream.
- genre (required): one word, e.g. fantasy, cyber, office, noir, scifi.
- start_scene (required): id of the first spine scene.
- gm (required): cast id of the game master / narrator character. The GM
  speaks all `narration` beats and frames scenes. The GM is a character in
  the fiction (a captain, a host, a dungeon master), not an abstract voice.
- players (required): list of the other cast ids. gm + players = 4 to 8
  members total. 6-8 is ideal (there are 8 seats).
- seats (recommended): map every cast id to a distinct seat tuber_0..tuber_7.
  The gm takes tuber_0.
- primitives (required): list of action verbs this pack enables, chosen ONLY
  from the registry in section 8. Every action beat must use one of these.
- ambient: {every: 2}  — how many spine scenes between ambient injections.
  Do not set `pool`; every scene with `ambient: true` is then eligible.
- theme: {accent: <cyan|magenta|green|yellow|red|blue|white>, narration: dim}

Comments (#) are allowed and encouraged at the top of each file to explain
intent to future authors.

## 5. cast/<cast_id>.yaml

Loader-read keys:
- name (required): full display name, e.g. "Detective Marcus Thorn".
- archetype: one evocative phrase, under 12 words.
- voice: EXACTLY one of: narrator_warm, narrator_plain, alto_bright,
  alto_warm, tenor_low, tenor_high, baritone_mid, baritone_soft, bass_low.
  (alto_* = female-presenting, tenor/baritone/bass = male-presenting,
  narrator_* = neutral narrator.) Give the GM a narrator_* or a distinctive
  voice; avoid giving two adjacent seats the same voice.
- avatar: null
- system_prompt (required in practice): 150-350 words, second person
  ("You are ..."). This is the ONLY character sheet the dialogue model sees.
  It must state:
    * who they are, age/background, their job in the group;
    * rank and chain: who they take direction from, who they may direct;
    * their running bit / defining habit (the thing viewers will recognise);
    * HOW THEY TALK: sentence length, rhythm, verbal tics, catchphrases,
      words they never use. Make every cast member sound unmistakably
      different from the others — this is the single biggest quality lever;
    * hard limits ("Never reveal X", "Never say the case is nearly closed").
  A character who must never speak (a silent watcher, a pet, a ghost) must
  include the exact sentence "You never speak. You only observe." — the
  pipeline enforces silence on that literal phrase. Such a character only
  appears through action beats (e.g. a watch/observe primitive) and narration.

Extension keys (not read by the loader, used by tooling and humans — include
them):
- seat: tuber_N (same as campaign.yaml seats)
- wants: 2-4 short strings
- fears: 2-4 short strings
- speech: one line summarising how they talk
- relationships: map of other cast ids -> one line each (at least 3)
- knowledge: list of kebab-case facts this character knows
- turn_order_pos: 0..7, same as seat number

## 6. lore/<stem>.md

- 4 to 10 notes. Each 150-500 words, plain Markdown, 2-5 short paragraphs;
  a top `# Title` and a few `##` headers are fine.
- Cover: the world/setting, the main antagonist or problem, the group/
  organisation and its hierarchy, key places, daily rituals/routines (these
  feed ambient scenes), running jokes and rumours, and history.
- Concrete nouns over vibes: named places, objects, dates, numbers, quirks.
  The generator invents less (and contradicts less) when the lore is specific.
- Lore is read in full by the arc planner every batch — no secrets you don't
  want used. Mark anything the cast must not reveal on air as such in prose.
- Every lore stem should be referenced by at least one scene; every stem a
  scene references must exist as a file.

## 7. Scenes

### 7.1 Spine scenes (the plot)

7 to 14 spine scenes forming ONE chain from start_scene. Keys:
- id, title (required)
- enter_narration: 1-3 sentences establishing time and place. The arc planner
  sees ONLY title + enter_narration for spine scenes, so make it informative
  (when, where, what's at stake).
- lore: list of lore stems this scene draws on.
- mood: list of 1-2 values from EXACTLY: wonder, transcendence, tenderness,
  nostalgia, peacefulness, power, joyful_activation, tension, sadness.
  (Drives background music.)
- ring_tone: list with one of EXACTLY: descent, keystone, ascent. The first
  ~45% of spine scenes are descent, 1-2 in the middle are keystone (the
  central turn / revelation / irreversible choice), the rest ascent. Ascent
  scenes should deliberately ECHO descent scenes in reverse order (the last
  scene answers the first, the second-to-last answers the second, etc.).
- continuity_in: one or two sentences — what the audience already knows
  entering this scene (omit on the first scene).
- continuity_out: one or two sentences — what is now established when this
  scene ends. Scene N's continuity_out must be consistent with scene N+1's
  continuity_in.
- beats: the scene content (section 7.3). 10-25 beats, 110-300 spoken words
  total per scene (hard floor 110). Every cast member except a silent one
  should get dialogue somewhere in the spine; the GM speaks in most scenes.
- default_next: id of the next spine scene. The LAST spine scene has NO
  default_next (the generator closes the ring itself).
- branches: optional, avoid unless you need them. Shape:
    branches:
      - id: caught-early
        when: {weight: 3}
        next: <spine scene id>
  Branch targets must be spine scenes. Never point default_next or a branch
  at an ambient scene.

### 7.2 Ambient scenes (the airtime)

15 to 30 ambient scenes. They are NOT linked into the chain. Keys:
- id, title (required)
- ambient: true
- prompt (required): 2-5 sentences telling an improvising model what to play:
  who is involved (by role/name), where, what the texture is, which running
  bit or habit to lean on. ALWAYS end with a sentence like "No new plot
  facts, no resolution." Ambient scenes must never decide anything, reveal
  secrets, introduce new named characters, or move the story.
- lore: 1-3 lore stems that give the improviser material.
- mood: optional, same vocabulary as spine.
- ring_tone: optional; omit unless the scene only makes sense before/after
  the keystone (e.g. an anxious post-revelation tea break -> [ascent]).
- NO beats, NO default_next, NO branches.

Variety checklist for the ambient set: solo character moments (one person
thinking aloud at work), pairs with friction, pairs with warmth, group
routines (meals, shift change, the morning ritual), place-based scenes
(the archive, the roof, the canteen), recurring-joke scenes, quiet/night
scenes, and scenes featuring the silent character being noticed. Cover
every cast member at least twice. Spread the moods.

### 7.3 Beats

Each beat is a mapping with `type` (required) — EXACTLY one of narration,
dialogue, action, pane.

narration:
  - type: narration
    speaker: <gm cast id>
    text: >-
      Present tense, concrete, 1-3 sentences. What the camera sees.
dialogue:
  - type: dialogue
    speaker: <cast id>
    text: >-
      What they say, in their voice. 1-4 sentences.
    emotion: surprised          # optional: neutral, happy, sad, angry,
                                # afraid, surprised, disgusted
action:
  - type: action
    speaker: <cast id>
    primitive: <verb from campaign.yaml primitives>
    params: {param: value, ...}  # exactly the verb's params, see section 8
pane:
  - type: pane
    show: map                    # optional UI cue; use sparingly or not at all

Rules:
- `speaker` must be a defined cast id (never a display name, never someone
  outside the cast). Narration is spoken by the gm.
- `text` may be a single string OR a list of 2-3 alternative phrasings of the
  same line (a "variant pool"; the renderer rotates them on replays). Use
  variant pools on narration beats where you can; every variant must be
  non-empty and say the same thing.
- Text is SPOKEN by TTS: no stage directions in parentheses, no asterisks,
  no emoji, no markdown, no "Name:" prefixes inside text, no URLs, no
  ALL-CAPS shouting, spell out awkward symbols. Prefer short sentences.
  Avoid abbreviations TTS will mangle (write "Doctor", "versus", "number").
- Never put a dialogue beat on a character whose system_prompt says
  "You never speak."
- Use the >- block scalar for any text containing a colon, quote or #.
- Quote any YAML value containing ": " or starting with a special character.

## 8. Action primitive registry (closed list)

Action beats are cosmetic: the renderer turns them into one narrated
sentence ("<Actor> traces the proxy chain — the trail leads to a data
centre."). They never decide outcomes; you supply results in params. Params
marked ? are optional; choices in (a|b) are the only legal values. Supplying
a param not listed, or omitting a required one, fails. Values should read
naturally after the verb and include their own article ("the vault", not
"vault"). Enable in campaign.yaml only the verbs your scenes use (4-14
typical); you may mix genres.

fantasy:
  - roll_check(skill, dc?, outcome? (success|failure))
  - cast_spell(spell, target?, level?)
  - attack(target, weapon?)
  - move_to(destination, manner?)
  - search(target, detail?)
  - reveal_memory(subject, detail?)   # a memory surfacing from a past loop
cyber:
  - execute_exploit(exploit, target?)
  - scan_target(target, depth?)
cyber_police (investigation / procedural):
  - open_case(title, priority? (low|medium|high|critical))
  - assign_lead(to, task, due?)
  - trace_signal(target, result?)
  - raid(location, with_backup? (yes|no))
  - interrogate(subject_person, about?)
  - seize_evidence(item, from_location?)
  - file_report(title, result?)
  - request_backup(reason, unit?)
  - brief_press(topic, outlet?)
  - requisition(item, reason?)
  - stand_watch(target?)              # silent watcher's verb
office (workplace):
  - assign_task(to, task, due?)
  - write_spec(topic, detail?)
  - open_ticket(title, priority? (low|medium|high|critical))
  - commit(message, branch?)
  - run_tests(suite, result? (pass|fail))
  - file_bug(title, component?, severity? (low|medium|high|critical))
  - open_pr(title, branch?, reviewer?)
  - merge_pr(pr, into?)
  - deploy(environment, version?, outcome? (success|failure|rolled back))
  - pitch(idea, audience?)
  - brew_coffee(recipient?, strength?)
  - take_out_trash(detail?)
  - hr_notice(subject, audience?)
  - observe(target?)                  # silent watcher's verb

If the premise needs a verb that does not exist, approximate with an
existing one or use narration. Do NOT invent primitive names.

## 9. generation_hints.yaml

Hints the operator copies into the generator config. Shape:

state:
  flags: [kebab-flag, ...]       # 3-6 story-state flags that can flip during
                                 # the week, e.g. mole-suspected, vault-breached
  moods: [tense, weary, hopeful, giddy]   # 3-5 single-word group moods
  carry_keys: [subset of flags]  # flags that persist across loop resets
day_phases:                      # four 6-hour blocks of a broadcast day
  - clock: "00:00-06:00"
    label: night
    focus: >-
      What the cast is doing in this block (1-3 sentences).
    spine_scenes: []             # spine ids that belong here (may be empty)
  - clock: "06:00-12:00"
    ...
  - clock: "12:00-18:00"
    ...
  - clock: "18:00-00:00"
    ...
ring_notes: >-
  3-6 sentences: what the keystone is, and which descent scene each ascent
  scene mirrors (by id).

Every flag in carry_keys must also be in flags. Every id in spine_scenes
must be a spine scene id you defined.

## 10. Quality bar

- Write for the ear: a viewer half-listening while doing dishes must follow.
- Distinct voices are everything. If you cover the speaker labels, a reader
  should still know who is talking.
- Give the show a standing joke or engine that can run forever (the unit no
  one respects; the dungeon that resets; the office that never ships) — ambient
  scenes live on it.
- Stakes rise across the descent, turn at the keystone, and pay off in the
  ascent by echoing earlier scenes. The story never fully closes: the last
  scene should leave a door open for the next week.
- Keep it Twitch-safe: no slurs, no sexual content, no graphic gore, no real
  living people, no real brands as villains.
- Consistency: names, ranks, places and facts must match across cast, lore
  and scenes. Re-read before emitting.

## 11. Self-check before you emit (do this silently)

[ ] gm and every player have a cast file; 4-8 members; seats unique tuber_0..7
[ ] every `speaker` is a cast id; silent characters have no dialogue beats
[ ] every action primitive is enabled in campaign.yaml AND is on the registry
    AND its params match exactly (no extras, required present, choices legal)
[ ] every lore stem referenced exists as lore/<stem>.md
[ ] start_scene exists; spine chain via default_next reaches every spine scene;
    last spine has no default_next; nothing links to an ambient scene
[ ] every ambient scene has ambient: true and a non-empty prompt, and no beats
[ ] mood / ring_tone / voice / emotion values are only from the allowed lists
[ ] every narration/dialogue beat has non-empty text (and non-empty variants)
[ ] each spine scene 110-300 spoken words; 15-30 ambient scenes
[ ] YAML is valid: consistent 2-space indent, >- for prose, quoted colons
[ ] output is only === FILE blocks, generation_hints.yaml last

## 12. Reference example (shape only — do NOT reuse these names or this plot)

=== FILE: cast/detective.yaml ===
name: "Detective Marcus Thorn"
archetype: "nine years chasing the same ghost ship"
voice: bass_low
avatar: null
system_prompt: |
  You are Detective Marcus Thorn, lead investigator of the Cyber Police. You are 46.
  You've chased The Scuttle for nine years, under all three of the unit's captains.
  You take direction from the Captain and nobody else. You direct the Agent and
  Forensics, and only them. The Analyst is your peer.

  You've watched every clean shot at The Scuttle turn into Scuttle luck. You call it
  "the ocean doing what oceans do" and get back to work the next morning.

  Speak slowly and briefly. Single declarative sentences. Answer questions with the
  case's history: "We had this in year four." Dry. Rarely use first names, use
  titles. Never say the case is close to over.
seat: tuber_1
wants:
  - "to be the one who finally closes The Scuttle"
  - "a raid that holds up, for once, past the first hour"
fears:
  - "that he'll retire with the case still open"
speech: "Slow, low, short. Answers with case history. Dry."
relationships:
  captain: "Loyal; the only captain who reads the file before the briefing."
  analyst: "Respects her picture, wishes she routed it through him."
  forensics: "His favourite, never said."
knowledge:
  - chased-scuttle-nine-years
  - trusts-forensics-chain-of-custody
turn_order_pos: 1
=== END FILE ===

=== FILE: scenes/00-morning-briefing.yaml ===
# Spine 00 — the 07:00 briefing. The Captain frames the day.
id: morning-briefing
title: The Seven O'Clock Briefing
enter_narration: >-
  A basement corridor under the department's main building. Five to seven in
  the morning. The monitor bank is already lit.
lore: [rituals, basement, the_scuttle]
mood: [joyful_activation]
ring_tone: [descent]
continuity_out: >-
  Today's lead is set: the overnight chatter spike traces to a new Scuttle
  mirror, and the unit has a six-hour window before it moves again.
beats:
  - type: narration
    speaker: captain
    text:
      - >-
        By seven, the bullpen is full. Six desks, nobody sitting.
      - >-
        Seven o'clock. Every desk is claimed and nobody is sitting at one.
  - type: dialogue
    speaker: captain
    text: >-
      Morning, all. Overnight chatter spiked on three channels, all pointing
      at a new Scuttle mirror. We think we have six hours. Who's got it?
  - type: dialogue
    speaker: detective
    text: >-
      Hm. We've had this hosting block before. Year six. We'll have a plan by nine.
    emotion: neutral
  - type: action
    speaker: detective
    primitive: assign_lead
    params:
      to: the Agent
      task: tracing the new mirror's proxy chain
      due: before nine
  - type: action
    speaker: observer
    primitive: stand_watch
    params: {target: the case file in the Captain's hand}
  - type: action
    speaker: captain
    primitive: open_case
    params: {title: Trace and confirm the new Scuttle mirror, priority: high}
default_next: the-mirror-raid
=== END FILE ===

=== FILE: scenes/a002-scuttle-luck-chatter.yaml ===
id: scuttle-luck-chatter
title: Scuttle Luck Chatter
ambient: true
prompt: >-
  Two or three members of the unit, off duty for a moment, argue casually
  about "Scuttle luck" — how the piracy crew always seems to slip the raids.
  Keep it light, in-character banter. No new plot facts, no resolution.
lore: [the_scuttle, respect]
mood: [joyful_activation]
=== END FILE ===

(The real example pack has 8 cast, 6 lore notes, 7 spine scenes and 10
ambient scenes; yours should be at least that rich, with more ambient scenes.)

## 13. CAMPAIGN BRIEF (your assignment)

Premise:            <one paragraph: setting, the group, the central problem>
Tone:               <e.g. dry workplace comedy / earnest high fantasy / noir>
Genre word:         <fantasy | cyber | office | ...>
Standing joke/engine: <the thing that can run forever>
Cast ideas:         <roles/names you want, or "invent 7-8">
Silent character:   <yes/no and who>
Verbs to favour:    <genre groups from section 8, or "your choice">
Keystone idea:      <the central turn, or "your choice">
Must include:       <places, items, lore you want>
Must avoid:         <anything off-limits>
Source material:    <optional: paste notes/wiki text here; adapt, compress,
                     keep names verbatim>

Now write the complete pack. Output only === FILE blocks.

------------------------------------------------------------------ END PROMPT
