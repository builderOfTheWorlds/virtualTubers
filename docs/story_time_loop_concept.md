# Story Concept — Simulation Time Loop

> **Status**: DRAFT / concept phase — narrative design, not yet implemented in
> any campaign config.
> **Written**: 2026-08-02
> **Purpose**: canonical, up-to-date record of the show's overarching premise
> and the mechanics that make it work (sage role, antagonist, knowledge
> propagation). Source brainstorm lives in
> [`notes/vtubers-brainstorm-notes.md`](../notes/vtubers-brainstorm-notes.md)
> — that file is the informal scratchpad; this doc is the cleaned-up
> reference. The first concrete story built on this premise is outlined in
> [`docs/dnd_campaign_pilot_outline.md`](dnd_campaign_pilot_outline.md).
> Canon for the actual setting (Duke Leto, Sultan Muad'Malik, The Event) is
> being adapted from a pre-existing homebrew campaign — see
> [`docs/ashiorid_campaign_analysis.md`](ashiorid_campaign_analysis.md) for
> what's established vs. invented. The 168-hour production blueprint built on
> all of the above is [`docs/weekly_loop_script.md`](weekly_loop_script.md).

## Premise

A weekly, 24/7 continuous stream built around a single-player-cast
simulation that resets every week. Each "loop" plays out as a scripted,
branching interactive story. Cast, genre, and world can change loop to loop
(fantasy one week, cyberpunk the next), but every genre is a different
"simulation layer" of the same overarching sci-fi frame: the characters are
trapped inside a simulation that keeps resetting, and are slowly uncovering
the truth. See the brainstorm notes for the full differentiator list
(genre-agnostic engine, branching/replayable narrative, viewer-driven
mechanics, CLI presentation).

The in-universe time-loop premise is a deliberate production hack: it gives
a free, no-narrative-bridge explanation for weekly resets, genre changes,
and content drops ("what simulation are we in this week?"), while building a
long-term mystery arc to retain viewers across real weeks.

## The Sage Role

- The sage is a **role**, not a fixed character. In the source D&D material
  it started as a single immortal (an elf who survived the original
  world-ending reset and lived 10,000 years as the party's sole source of
  information) — that fixed character is now the *origin point* of a role
  that rotates through the cast going forward.
- **Tenure**: two loops (two weeks) per sage. A character is anointed at the
  start of one loop and holds the role through the end of the following
  loop before being replaced.
- **Handoff needs two characters, not one**: the outgoing sage (finishing
  their tenure) and the incoming sage-designate (a fresh character stepping
  into the role) are both active on stream during the transition — a
  relay/mentorship, not an instant identity swap.
- **Two-stage knowledge unlock**:
  1. Mid-loop, any character can actively seek out the current sage for a
     partial memory-infusion (not automatic, not free).
  2. Full knowledge only transfers to whichever character becomes the
     *next* sage.
- Every new loop's cast starts with **zero memory** of their own past or the
  loop itself. The sage is the only bridge between loops.

**Open question**: is succession a *deliberate choice* — the outgoing sage
grooms a specific character during the week — or is it still governed by
the original "whoever happens to be at the reset-trigger location" rule?
These produce different stories (a sage who chooses her heir vs. one who
gets whoever's randomly standing there) and haven't been reconciled yet.

**Open question**: does the outgoing sage need to physically re-trigger at
the reset location to carry their own tenure into the second loop, or does
the second loop happen automatically once anointed?

## The Antagonist

Deliberately differentiated from the sage. In the original source story
both the sage and the antagonist became immortal after "the event" — for
this format that's being split apart:

- The antagonist is a **static, singular character**, not a rotating role.
  He remembers every loop, unbroken, across thousands of years.
- **The kicker**: that unbroken memory — endless repeated failure to escape
  the loop — is slowly warping his mind. He gets less stable/rational the
  older (in loop-count) he gets.
- In-fiction, his escape attempts are framed to each loop's cast as an
  attempt to destroy the world. The cast stops him without realizing his
  real goal was leaving, not destruction.

**Open question — not fleshed out yet**: how the mental decay is paced or
shown. Gradual loop-by-loop drift the audience can track as a meta-arc, or
bigger jumps revealed at specific story beats? Needs its own design pass.

## Knowledge Propagation Rules

- **Hard wipe** for everyone except the sage line — no other character
  retains anything between loops.
- Failure inside a loop (e.g. a full party wipe) does **not** trigger an
  early reset. The loop always runs to the scheduled weekly reset
  regardless of story state.

## Decisions Log (2026-08-02)

| Topic | Decision |
|---|---|
| Failure handling | Bad outcomes never cut a loop short; runs to scheduled reset. |
| Residual knowledge | Hard wipe for everyone but the sage line. |
| Antagonist vs. sage | Antagonist is static/singular; sage is a rotating role. Deliberately different mechanics. |
| Sage tenure | Two loops (two weeks) per sage, handoff needs two characters present. |

## Parked / Open Items

- **Wipe contingency** (parked, not being worked right now): what actually
  keeps the stream going if the entire main cast goes down mid-loop —
  backup characters, other NPCs stepping in, an in-fiction device that
  revives/replaces the party at a cost?
- **Duke/convener recurring role**: undecided whether the duke figure is
  also a cross-genre recurring role like the sage. Revisit later.
- **Sage succession mechanism** (see above): deliberate mentorship vs.
  reset-trigger-location randomness.
- **Antagonist decay** (see above): not fleshed out.
