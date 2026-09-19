# Ambient scene expansion — brainstorm for review (2026-09-18)

## Why this exists

The pack currently has 10 ambient scenes. Reaching 168h of distinct content
by generating more *takes* of the same 10 prompts means heavy, noticeable
repetition (`chadwick-lost` alone hit 280 takes — hours of the same joke).
The fix is more distinct scene **premises**, not more takes per premise.

Rough math: at a viewer-reasonable cap of ~20-30 takes/scene before a scene
starts feeling reused, reaching 1.5M words needs on the order of 150-250
distinct ambient scenes, not 10. This batch is a first tranche toward that,
not the whole way there — meant to be reviewed, trimmed, and expanded in
further batches once we see how it plays.

## Ring-phase tagging (ties ambient content to the 3-layer generator's cycles)

The arc planner (`utilities/3LayersWeeklyGeneration/`) builds each 168h loop
as a ring: `descent -> keystone -> ascent`, with each arc segment carrying a
`plot_path` role. Right now ambient scene *selection* (which filler plays
between spine scenes, in `app/campaign/pack.py` / `ambient.py`) has no idea
which ring phase the loop is currently in — it just round-robins through
whatever's in the pool. That means a light joke scene (`chadwick-lost`) is
exactly as likely to land right before the keystone crisis as during the
quiet opening descent, which undercuts the pacing the ring structure is
built to create.

**Proposed schema addition** (new optional key on ambient scenes, additive —
scenes without it stay eligible everywhere, same as today):

```yaml
id: chadwick-lost
ambient: true
ring_tone: [descent, ascent]   # NEW, optional. Omit = eligible in any phase.
prompt: |
  ...
```

Closed vocabulary: `descent`, `keystone`, `ascent`. A scene may list more
than one. This is a filter on *selection*, not a hard partition — the
existing `ambient.every`/`pool` mechanism doesn't need to change, and a pack
with no `ring_tone` anywhere degrades to exactly today's behavior. Wiring
this into `ambient.py`'s selection logic is a small follow-up once the scene
content itself is approved — flagging it here so the content is authored
with phase in mind from the start rather than retrofitted.

Rough intent per phase:
- **descent** — light, character-building, low stakes. The "everything is
  still normal" texture. Comedy and small character beats live here.
- **keystone** — nothing ambient should play IN the keystone itself (that's
  the arc's own generated content), but scenes tagged keystone-adjacent
  carry tension, dread, or the specific business of the crisis at hand.
- **ascent** — aftermath tone. Consequences, quieter character beats,
  things earned or lost showing on-screen. Not comedy-neutral, but the joke
  has to know what just happened.

Below, each scene proposal is tagged with its intended phase(s).

---

## Existing 10 scenes (for reference, un-tagged today)

| id | current take count | rough tone |
|---|---|---|
| camp-fire | 280 | descent, light |
| chadwick-lost | 280 | descent, comedic |
| Leena-almost | 277 | keystone-adjacent — the ONE existing scene that brushes the arc's central mystery (Leena's origin), deliberately never resolving it |
| Vigil-counts | 277 | descent, quiet/observational |
| malmont-market | 183 | descent, light |
| night-watch | 130 | descent/ascent, quiet |
| road-talk | 130 | descent, light |
| sodacan_bob-on-craft | 130 | descent, light |
| sodacan_bob-on-runes | 127 | descent, light |
| the-meal | 130 | descent, light |

**Observation:** the existing 10 skew almost entirely toward light/descent
tone. `Leena-almost` is the sole scene with any thematic weight, and it's
explicitly designed to plant unease without paying off (correct for its
purpose — the payoff belongs to the spine, per its own header comment).
There's no keystone-adjacent tension material beyond that one scene, and
almost no ascent (aftermath) material. New scenes should correct that skew.

**Also flagging, not blocking:** `a06-carl-counts.yaml` and
`a07-helen-almost.yaml` are stale filenames — their `id:` was updated
in-place at some point (Carl→Vigil, Helen→Leena, presumably a
character/cast rename) but the files were never renamed to match. Harmless
today (the loader keys on `id:`, not filename) but worth a follow-up
cleanup so `ls scenes/` doesn't lie about what's in the pack.

---

## New scene proposals (batch 1 — 24 premises)

Grounded in the existing lore files and cast `system_prompt`s already in the
pack. Each follows the existing format: decide nothing permanent, no beats
needed, 6-10 lines, state length/medium in the prompt.

### Descent-tone (light, character, "everything is still normal") — 10

1. **`sodacan-forge-envy`** — Sodacan Bob finds a piece of local smithwork
   he begrudgingly admits is good, and hates that he has to admit it.
2. **`vigil-tracks`** — Vigil reads tracks on the road that mean nothing
   dangerous, just explains what passed through and when. A rare moment of
   him volunteering information unprompted.
3. **`leena-recognizes-nothing`** — Leena keeps almost-recognizing
   landmarks that she's never been to. Nobody remarks on it directly but
   everyone notices her noticing.
4. **`chadwick-oath-technicality`** — Chadwick argues (badly) that some
   minor rule-bending doesn't violate his oath. Nobody's convinced,
   including him.
5. **`weather-argument`** — The party argues about whether it's going to
   rain. It does or doesn't; the scene ends before anyone finds out.
6. **`nafsari-question`** — Someone asks Vigil an innocent question about
   growing up with the wood elves. He answers exactly as much as he wants to
   and not one word more.
7. **`bob-names-the-runes`** — Sodacan Bob explains what a rune on some
   mundane object actually does, showing off, while nobody asked.
8. **`carrying-the-signet`** — Someone notices the half-signet-ring one of
   them carries and asks about it. Deflection, not a revelation.
9. **`inventory-argument`** — A mundane argument about whose bag holds what
   and why nobody labeled anything.
10. **`local-rumor`** — They overhear an unrelated local rumor (something
    about Malmont's unregistered night-time power) that goes nowhere this
    scene. Plants unease without paying it off.

### Descent-tone, comedic (a few more of these ARE fine, just not 280) — 6

11. **`chadwick-cooking`** — Chadwick insists on cooking. It's bad. Everyone
    eats it anyway out of solidarity, badly disguised as hunger.
12. **`bob-vs-the-cart-wheel`** — Sodacan Bob has strong, loud opinions
    about the party's cart's wheel construction and will not let it go.
13. **`leena-tries-slang`** — Leena picked up a piece of local slang
    wrong and uses it confidently and incorrectly all scene.
14. **`vigil-almost-jokes`** — Vigil almost makes a joke. It's very nearly
    funny. Nobody's sure if he meant to.
15. **`the-map-argument`** — An argument about map-reading that isn't
    Chadwick's fault this time, for once, and he's insufferable about it.
16. **`bob-sings`** — Sodacan Bob sings a dwarven work-song badly and
    completely without embarrassment.

### Keystone-adjacent (tension, dread, the specific stakes at hand) — 4

17. **`before-the-well`** — Quiet dread the night before approaching a
    moonwell. Nobody sleeps well. Nobody says why out loud.
18. **`bahadur-sighted`** — A distant, wordless sighting of something that
    might be Bahadur. Nothing happens. It doesn't need to.
19. **`the-ring-half`** — A quiet scene where the signet-ring half is looked
    at directly, not deflected this time. What it might mean, unresolved.
20. **`grovley-remembered`** — The party talks about Grovley and what he
    didn't tell them. Suspicion without accusation.

### Ascent-tone (aftermath, consequence, earned quiet) — 4

21. **`after-the-choice`** — Quiet aftermath following a major decision.
    Nobody's celebrating. Something was given up.
22. **`counting-the-cost`** — Bob assesses physical damage to gear/people
    after a hard stretch, in his own blunt idiom (forge and joinery
    metaphors, not medical ones).
23. **`leena-different-now`** — Leena notices something about herself has
    changed and doesn't have the vocabulary for it yet. Others notice her
    noticing.
24. **`vigil-watches-longer`** — Vigil takes a longer watch than his turn
    without saying why. Someone else notices and doesn't ask.

---

## What I need from you before I touch the pack

1. **Approve/cut/edit this list.** Anything that reads wrong for the
   characters, contradicts established lore, or just isn't wanted — say so
   and I'll drop or rewrite it.
2. **Confirm the `ring_tone` schema direction** above, or tell me to skip
   the ring-phase tagging for now and just add scenes untagged (simpler,
   but loses the cycles-in-cycles pacing tie-in you asked about).
3. **Confirm scale**: is ~24 new scenes a reasonable first batch, or do you
   want a bigger first pass before we generate any takes?

Nothing in `campaigns/ashiorid_1/scenes/` or `app/campaign/` has been
touched yet — this file is pure proposal.
