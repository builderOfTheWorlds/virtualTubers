# Ashiorid 168h Arc — Ring Composition Layout

Generated from the existing generator config (`generation.yaml`): 28 segments
x 6 hours = 168 hours, weekly loop reset at Sat/Sun 00:00. This diagram shows
where ring composition (A B C B' A' mirroring) sits ON TOP of that existing
segment tree — no schema changes, purely a content-planning overlay.

## Top level: the whole week is one big ring

```
 00:00 Sat ─────────────────────────────────────────────────────────► 00:00 next Sat
 (loop N start)                                                        (loop N+1 start)

 Seg 01-05        Seg 06-10       Seg 11-18        Seg 19-23      Seg 24-28
 (0-30h)          (30-60h)        (60-108h)        (108-138h)     (138-168h)
 ┌───────┐        ┌───────┐       ┌───────────┐    ┌───────┐      ┌───────┐
 │   A   │───────▶│   B   │──────▶│     C     │───▶│  B'   │─────▶│  A'   │──┐
 │depart │        │ party │       │ KEYSTONE  │    │ party │      │return │  │
 │ home  │        │ forms │       │  (mid-arc │    │proves │      │ /reset│  │
 │       │        │       │       │  crisis)  │    │ bond  │      │       │  │
 └───────┘        └───────┘       └───────────┘    └───────┘      └───────┘  │
     ▲                                                                       │
     └───────────────────────── LOOP BACK (state reset) ◄─────────────────────┘
```

- **A  (~0–30h):**  Hero leaves home — the Invitation / Age-of-War intro arcs.
- **B  (~30–60h):** Party forms, sets out together — Manor, party-attack, bahadur-reveal.
- **C  (~60–108h, the KEYSTONE):** Biggest mid-week crisis — vault / riddle / hardest trial.
  Sits at roughly the arc's midpoint by design; every other beat echoes toward or away from it.
- **B' (~108–138h):** Party overcomes together, mirrors B but the bond is now tested & proven.
- **A' (~138–168h):** Hero returns / disbands — mirrors A, but changed. Then LOOPS to A.

The mirrored pairs (A/A', B/B') are where callbacks, repeated phrases, and
"we've been here before, but—" beats should land — that's what makes the loop
feel like a **story**, not a re-run.

## Segment level: each segment can carry its OWN small ring

Every arc segment already gets its own recursive tree (`segment.tree` in
`generation.yaml` — `max_depth`, `max_children`, etc.). A ring can be laid
onto that tree the same way: assign each top-level child of the segment root
a ring role instead of a bare narrative label.

```
One segment (6h) — e.g. "letos-manor-arc"
┌────────────────────────────────────────────────────────┐
│ root                                                    │
│  ├─ child[0]  a   (arrival / setup)                     │
│  ├─ child[1]   b  (rising tension)                      │
│  ├─ child[2]    c (KEYSTONE beat — the manor's secret)  │
│  ├─ child[3]   b' (consequence, mirrors child[1])       │
│  └─ child[4]  a'  (exit / handoff to next segment)      │
└────────────────────────────────────────────────────────┘
```

This is fractal: the SAME a b c b' a' shape recurs at segment scale inside
each slice of the big A B C B' A'. A segment inside the big "C" keystone
block can itself have its own internal keystone — nested rings, exactly the
"smaller cycles inside the larger cycle" idea from Star Wars Ring Theory.

## Where the loop actually closes

```
 ...Seg 28 (A') ──text/effects──▶  carry_out: {} (state cleared)
                                          │
                                          ▼
 Seg 01 (loop 0)  ◄── same spine_scenes, same opening beats, `loop: N+1`
```

`arc.loop` (already in `arc_schema.py`'s required keys) is the field that
marks this: segment 01 of week N+1 reuses the same `id`/spine scenes as week
N's segment 01, just with `loop` incremented. That is the literal ring-closure
point — Saturday midnight always resets to "the hero leaves home," but each
loop's telling can vary its take-pool phrasing so it isn't verbatim repetition.
