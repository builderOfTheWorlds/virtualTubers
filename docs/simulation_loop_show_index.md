# Simulation Time Loop Show — Documentation Index

> **Status**: Navigation index for the full body of narrative-design work
> from 2026-08-02. Nine documents, cross-linked pairwise but with no single
> map until now — this is that map.
> **Scope**: the *story* design (premise, canon, weekly production, cast).
> For the *technical platform* that runs it (primitives, episode schema,
> worker architecture), start at
> [`campaign_platform_build.md`](campaign_platform_build.md) instead — the
> two bodies of work meet at `config/campaigns/dnd/`.

## Reading order

1. **[`story_time_loop_concept.md`](story_time_loop_concept.md)** — the
   premise. Weekly reset, the sage role (rotating, 2-loop tenure), the
   static antagonist warped by unbroken memory, knowledge-propagation
   rules. Start here.
2. **[`ashiorid_canon_reference.md`](ashiorid_canon_reference.md)** — the
   consolidated canon of the pre-existing homebrew campaign this show is
   built on: world, politics, magic system, NPCs, the four player
   characters, and the ambient-content engines the vault already contains.
   Read this before writing any scene — it's the single reference for what's
   real vs. invented.
   - [`ashiorid_campaign_analysis.md`](ashiorid_campaign_analysis.md) —
     narrower companion, kept specifically for *provenance*: which vault
     entries are authentic DM canon vs. import-time AI-generated scaffolding
     (the Grovley case is the cautionary example).
3. **[`season_01_arc.md`](season_01_arc.md)** — the 7-loop macro structure.
   Each loop is a complete story whose Friday midpoint carries one macro
   turning point. Contains the proposed **season anchor** (what the whole
   thing is actually about) — the single most important unlocked decision
   in this entire body of work, see below.
4. **[`weekly_loop_script.md`](weekly_loop_script.md)** — the production
   model for one loop: the six-block daily rhythm, the authored-spine vs.
   improvised-connective-tissue split that solves the 168-hour problem, and
   the day-by-day beat grid.
5. **[`loop_01_hour_by_hour.md`](loop_01_hour_by_hour.md)** — all 168 hours
   of loop 1, scened out, tagged by content class ([A]uthored /
   [S]emi-authored / [R]ecap / [I]mprovised).
6. **[`ambient_content_bank.md`](ambient_content_bank.md)** — concrete,
   pickable scenes for the [S]/[I] hours: the watch-pairing matrix, dream
   fragment bank, chat Q&A formats, the world-tick ballast mechanic.
7. **[`loop_01_character_bible.md`](loop_01_character_bible.md)** — the
   four PCs plus NPCs, written to be *generative* (each gets a hole, a tell,
   a refusal) since these personas have to improvise ~88 hrs/week. Fully
   canon-verified as of this pass.
8. **[`dnd_campaign_pilot_outline.md`](dnd_campaign_pilot_outline.md)** —
   superseded. Kept for history; its necromancer/phylactery plot was an
   illustrative placeholder from before the Ashiorid canon sweep, not the
   real setting.

Raw brainstorm source for all of the above:
[`notes/vtubers-brainstorm-notes.md`](../notes/vtubers-brainstorm-notes.md).

---

## Decisions still needed before loop 1 can be finalized

Pulled from all eight documents into one list, ranked by how much they
block. None of these are answered yet — they're the user's calls, not
mine.

### Blocking — affect nearly everything downstream

1. **Tone.** Every doc above is written grim/dramatic. The source campaign
   is broad, bawdy comedy (a PC named after a slur on a thunder-cock with
   commissioned heraldry for the joke; a Bob Ross parody; a werewolf that's
   a yappy pomeranian) sitting alongside real cruelty (a press-driven
   suicide, a priestess cursed to never tell the truth). See
   [`ashiorid_canon_reference.md`](ashiorid_canon_reference.md) §1. Nothing
   past this point can be finalized in a consistent voice until this is
   picked.
2. **The season anchor** — [`season_01_arc.md`](season_01_arc.md) §1
   proposes Duke Leto III deliberately architected the loop and the party
   are unknowing jailers of the antagonist. Everything in loops 2–7 is
   built on this holding. If rejected, the whole macro arc needs a new
   spine.
3. **The Buffalo/Carl parentage conflict** —
   [`loop_01_character_bible.md`](loop_01_character_bible.md) "Canon
   conflicts": two of the four PCs have named, non-Leto birth parents in
   their own source files. Three resolution options are laid out there,
   undecided. Blocks Tuesday's reveal scene in
   [`loop_01_hour_by_hour.md`](loop_01_hour_by_hour.md) as currently
   written.
4. **Sage succession mechanism** — deliberate mentorship (the outgoing sage
   chooses her heir) vs. happenstance (whoever's at the reset-trigger
   location). Recommended: happenstance, for the chat-stakes reason given in
   [`story_time_loop_concept.md`](story_time_loop_concept.md). Blocks Fri
   16:00, Sat 02:00/10:00, and Sun 23:00 in the hour-by-hour.

### Secondary — scoped to specific scenes, don't block starting

5. **Muad'Malik named on-screen in loop 1, or kept unnamed?** Recommended:
   unnamed (preserves a macro-arc card; canon itself leaves him unnamed at
   this point). — `weekly_loop_script.md` §9
6. **Invitation text: "Ten Thousandth Anniversary" vs. 1,000 years**
   (the source contradicts itself). Recommended: 1,000, matching the
   Timeline's math — and `season_01_arc.md` Loop 7 turns the discrepancy
   into a plot point either way.
7. **Wipe contingency** — parked since the brainstorm phase. Only becomes
   urgent if Saturday's darkest-hour beat is written to include a PC death.
8. **Voice catalog capacity** — the show needs 7+ distinct Piper voices;
   only 3 are currently claimed in `config/campaigns/dnd/personas.yaml`.
   Needs a check against `scripts/download_voices.py`'s catalog before
   personas are finalized.
9. **Genre-hop timing** — is Loop 3 (the cyberpunk layer) too early to
   break the fantasy setting, or exactly early enough to teach the audience
   what the show can do? — `season_01_arc.md` §6
10. **Season 2 format** — does the loop actually break at the finale, or
    does the show admit it's a perpetual format? Determines whether Loop 7
    is an ending or a hinge.

### Not blocking, just flagged

- **Sat 4-hour timezone assumption** in the daily rhythm (`weekly_loop_script.md`
  §3) — built for US-evening-prime; shifts if the audience skews EU/AU.
- **Meta/index vault sweep incomplete** — `Content Discovery Summary.md`,
  the root `README.md`, and a full dangling-wikilink extraction from
  `Campaign Index.md` were never read (agent hit a session limit). Possible
  free creative real estate still sitting unread. See
  [`ashiorid_canon_reference.md`](ashiorid_canon_reference.md) §12.
- **A recoverable asset**: `Resources/File Reference Index.md` names files
  never imported into the vault, including a `Mass NPC Generator.xlsx` and
  the real (priced) Shop Catalog `.docx`, still sitting at
  `C:\Users\matt\PycharmProjects\ashiorid\lore\DnD Campaign\` on the
  original author's machine.

### ⚠️ Safety — not a decision, an action item

`Resources/Cautionary Tales/SackWhores.md` in the source vault contains
graphic child sexual exploitation content, disguised by its folder name as
usable folklore. If anything in the production pipeline ever does
retrieval over that vault's `Resources/` folder, quarantine or delete this
file first. Full detail in
[`ashiorid_canon_reference.md`](ashiorid_canon_reference.md) §0.
