# VTubers Project — Weekly Simulation-Loop Show

## Stakeholder Summary

We're developing a new format for our AI VTuber streaming platform: a **weekly, 24/7 continuous stream** built around a single-player-cast simulation that resets every week. Each "loop" plays out as a scripted, branching interactive story, with the cast, genre, and world potentially changing week to week — a hacker campaign one week, a fantasy campaign the next — all tied together by an overarching sci-fi premise: the characters are trapped inside a simulation that keeps resetting, and are slowly uncovering the truth.

Key differentiators:
- **Genre-agnostic engine**: the same worker/agent architecture and action framework powers every campaign (coding, D&D fantasy, cyberpunk hacking, and future genres), so new content types can be added without rebuilding the system.
- **Branching, replayable narrative**: scripted decision points let outcomes vary run to run, supporting both live engagement and rewatch value.
- **Viewer-driven mechanics**: viewer count and chat participation can directly influence in-story events (team size, NPC behavior, story branches), turning the audience into an active participant rather than a passive viewer — in the spirit of "Twitch Plays."
- **Built-in continuity hook**: the in-universe time-loop premise gives a natural, low-cost explanation for weekly resets and new content drops, and creates a long-term mystery arc to retain viewers over time.
- **Distinctive presentation**: a CLI/terminal-style console (rather than avatar streaming) is the current visual identity — lower technical overhead, easier to keep in sync, and a unique aesthetic in the VTuber space.

Status: early concept/design phase. The D&D campaign has existing content and will be used as the first test case before expanding to other genres.

---

## Detailed Notes

### Architecture
- Multi-agent system with Manager/Coder/Tester-style agents, running in a `virtualTubers` codebase accessible via Claude Code.
- CLI-first MVP using tmux/neovim, with a RabbitMQ/Redis backend.
- Workers have been reworked into **generic classes** reusable across different "campaigns" — currently have Coders and D&D campaigns implemented.
- A shared set of **action primitives** is being built so any campaign can reuse the same underlying mechanics with different flavor (e.g. a hacker campaign's "execute exploit" action vs. a D&D campaign's "cast spell").

### Campaign Concepts

**Cyberpunk Hackers**
- Workers act as player personalities in a hacker RPG; a GM worker sets hacking objectives/scenarios (e.g. breach a corporate AWS environment).
- Actions include things like appearing to execute hacker code on stream (cosmetic/flavor, following preconfigured scripts).
- **Branching scenarios**: e.g. a breach attempt succeeds (continue infiltrating) or fails (find an alternate way in) — different branches support replay variety and could be gated by chat votes.
- **Two-team PvP mode**: a hacker team vs. a MegaCorp defense team, mirrored action sets on both sides.
- **Viewer-power meter**: viewer count could power or speed up a team's actions; reaching viewer thresholds could spawn additional team members (e.g. 100 viewers → 2nd hacker joins, escalating to 2v1, 2v2, etc.). Thresholds could be asymmetric per team as a catch-up mechanic.

**Overarching Meta-Concept: Simulation Time Loop**
- Origin idea: a hacker character discovers he's trapped in an AI simulation. This becomes the frame that lets *any* genre (D&D fantasy, cyberpunk, a mining civilization, etc.) exist as a different "simulation layer" the same underlying story passes through.
- Combined with a **time loop**: each loop is a full reset of the simulation. This gives an in-universe reason for replays and branch variety — every stream literally is another iteration of "the same event," with the audience watching what's different this time.
- Likely influenced by *The Legend of Zelda: Majora's Mask* (a dying world, reset via time travel, NPCs on a learnable schedule).
- **Weekly show format**: the stream runs 24/7 for a full week per loop, with the reset marking the boundary between one week's show and the next. "Last week" in-fiction literally means "the previous loop."
- Genre-hopping between loops requires no narrative bridge — the reset itself is the transition ("what simulation are we in this week?").

### D&D Campaign Backstory (source material / first test case)
- A 10,000-year time loop in which the antagonist is trying to *break out* of the loop — originally framed to players as an attempt to destroy the world, which they end up thwarting (not realizing his real goal).
- A small set of **loop-aware NPCs** recruit the player characters each cycle. One is an elf sage who survived the original world-ending reset (she was at a mountaintop magic well/portal when demons invaded and the world reset) and can project her memories directly into players' minds to convey history.
- The player-character party are secretly the duke's progeny, born through a hidden magic-attuned breeding program, raised believing they were orphans, and brought together via an invitation to the duke's castle.
- This D&D campaign's existing notes/content will be the **first test scenario** for the new architecture, though there may not be enough material yet to fill a full week of 24/7 stream.

### Sage Role & Knowledge Propagation
- The "sage" is a **role**, not a fixed character: whoever happens to be at the reset-trigger location when a loop resets becomes the next loop's sage. (Cyberpunk equivalent: whichever hacker is logged into the right machine when the reset hits.)
- **Two-stage knowledge unlock**:
  1. Mid-loop, players must actively seek out the current sage to receive a partial memory-infusion (not automatic at loop start).
  2. Full/complete knowledge only transfers to whichever character becomes the *next* sage — propagating that character's personality forward into the sage role.
- Each new loop's cast starts with **zero memory** of their own past or the loop itself — the sage is the only knowledge bridge between loops.

### Chat / Viewer Interaction
- Not yet decided whether chat interaction launches at day one or is added later.
- Strong interest in "Twitch Plays"-style mechanics — likely applied to **NPC behavior/actions** rather than direct control of the main player-character workers, to keep the scripted narrative intact while still giving chat real influence.

### Presentation
- Current direction: keep the **CLI console** presentation (vs. full vtuber avatar streaming) — avatars are a unique medium but come with sync/reliability overhead.
- Separate panes/windows for character faces & lines, with the ability to switch to other panes (maps, inventory, combat, etc.).
- Idea to **evolve the CLI's look over time** as a visible marker of weekly continuity/progress (e.g. visual changes between loops or genres).
- Streaming to Twitch currently; multi-platform support is a future goal.

### Open Questions
- How dead-end/failure branches should be handled (not yet worked out) — e.g. does a bad outcome trigger an early reset, or continue to the scheduled reset time regardless?
- Whether the "duke"/convener figure and the antagonist are also recurring cross-genre roles (like the sage), and if so, whether they know about each other or the loop.
- Whether other surviving characters (not just the new sage) retain any residual knowledge between loops, or whether the reset is a hard wipe for everyone but the sage line.
