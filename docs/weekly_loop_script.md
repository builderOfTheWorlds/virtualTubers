# Weekly Loop — 24/7 Production Script

> **Status**: DRAFT v1 — production blueprint, not yet broken into episode
> JSON or campaign config.
> **Written**: 2026-08-02
> **Interpretation note**: "script" here means the **production script for a
> week** — what airs when across 168 continuous hours, with Loop 1 filled in
> concretely against Ashiorid canon. It is not line-by-line dialogue. Sample
> lines appear only where they pin down tone. If you wanted literal dialogue,
> say so and this becomes the outline for that instead.
>
> **Companions**:
> [`loop_01_hour_by_hour.md`](loop_01_hour_by_hour.md) — the full 168-row
> operational schedule built from this doc; read this one for the *why*, that
> one for the *what airs at 03:00 Thursday*.
> [`story_time_loop_concept.md`](story_time_loop_concept.md) (premise +
> mechanics), [`ashiorid_campaign_analysis.md`](ashiorid_campaign_analysis.md)
> (what's real canon vs. invented),
> [`dnd_campaign_pilot_outline.md`](dnd_campaign_pilot_outline.md) (earlier,
> superseded beat draft — this doc replaces its schedule).

---

## 1. Structure at two scales

**The small ring** — one loop = one week = one complete story. Nine beats,
genre-agnostic. A viewer who watches exactly one week gets a whole story
with a beginning, middle, and end.

**The big ring** — seven loops = one macro season. Each loop's **Midpoint
Turn** (beat 5) doubles as one of the seven macro turning points. A viewer
following all seven gets the time-loop mystery arc resolving underneath the
weekly stories.

The macro arc is authored **in real time** — position N+1 gets written after
loop N has actually played out, so viewer votes and in-story outcomes feed
forward. Only the *shape* is fixed in advance.

| Macro pos. | Loop | What lands here (shape only) |
|---|---|---|
| 1 | Loop 1 | Normalcy + first glitch. Audience doesn't know it's a loop. |
| 2 | Loop 2 | First real evidence of repetition. |
| 3 | Loop 3 | Consequences from loops 1–2 compound. |
| 4 | Loop 4 | **Macro midpoint** — the loop's nature or the antagonist's real goal cracks open. |
| 5 | Loop 5 | Fallout reframes everything prior. |
| 6 | Loop 6 | Darkest hour at season scale. |
| 7 | Loop 7 | Season climax — breaking, or failing to break, the loop. |

> **Anchor caveat**: real-time authoring risks the mystery-box failure mode
> (no endgame, audience loses faith). Lock a private answer to *"what does
> loop 7 actually reveal, and what happens to Muad'Malik"* before airing loop
> 1. The path stays improvised; the destination does not.

### The nine beats (small ring)

| # | Beat | Function |
|---|---|---|
| 1 | Introduction | Cast introduced separately, in their own lives |
| 2 | The Call | Inciting incident; they're pulled in |
| 3 | Coming Together | Strangers forced into a group; friction |
| 4 | Trials & Bonding | Escalating hardship forces interdependence |
| 5 | Midpoint Turn | Reveal/reversal; **carries the macro beat** |
| 6 | Darkest Hour | Setback that tests the bond |
| 7 | Climax | Confrontation with the antagonist |
| 8 | Resolution | Victory and aftermath |
| 9 | Loop Reset | The weekly reset; sage handoff |

---

## 2. The runtime problem, and the answer

168 hours per week. Authored story beats realistically cover 40–55 of them.
The gap is not closed by writing more plot — it's closed by splitting the
week into two content classes:

- **Authored spine (~56 hrs)** — the nine beats. Pre-written, plot-bearing,
  irreversible. These are checkpoints the show must hit.
- **Agent-improvised connective tissue (~112 hrs)** — generated live by the
  worker agents inside hard constraints. Fills time, builds character,
  advances nothing irreversibly.

This is what the platform is actually *for*. The agents already improvise in
character; the beats just give them fixed points to improvise between.

### Constraint rules for improvised blocks

An improvised block **must not**:
- advance the plot to a state the next authored beat doesn't expect
- kill, permanently injure, or remove a cast member
- reveal macro-arc information (loop nature, antagonist's real goal)
- move the party to a different location than the block started in

An improvised block **should**:
- end in roughly the state it began (positionally and materially)
- deepen character, relationships, or world texture
- be safely skippable — a viewer who misses it loses nothing plot-critical

---

## 3. The daily rhythm

Every day runs the same six-block shape. Viewership assumptions are
US-Twitch-centric; adjust if the audience skews elsewhere.

| Block | Hours | Viewership | Content class | Purpose |
|---|---|---|---|---|
| **Deep Night** | 00:00–06:00 | lowest | Improvised | Camp, watch rotation, dreams, chat Q&A. Zero irreversible plot. |
| **Dawn** | 06:00–09:00 | low | Semi-authored | Recap block — GM "previously on", map review, planning. Catches up anyone joining. |
| **Day** | 09:00–15:00 | medium | Improvised + light authored | Travel legs, side encounters, NPC interaction, skill challenges. |
| **Golden** | 15:00–18:00 | rising | Authored | Build-up: approach, mounting tension, setup for the anchor. |
| **PRIME** | 18:00–23:00 | **peak** | **Authored** | **The day's anchor beat. The real plot happens here.** |
| **Late** | 23:00–00:00 | falling | Semi-authored | Aftermath, decompression, chat reaction, cliffhanger stinger. |

Hour math: (6 + 3 + 6 + 3 + 5 + 1) × 7 = **168** ✓
Authored: (Golden 3 + Prime 5) × 7 = **56 hrs**. Everything else improvised
or recap.

**Why this shape**: on a week-long continuous stream there is no single
audience — nobody sees it all. So the plot must be concentrated where the
most eyes are (Prime), the dead hours must be non-critical (Deep Night), and
every day must open with a recap (Dawn) because most viewers on any given
day missed yesterday.

---

## 4. The week grid

Reset lands **Sunday ~22:00** — the single biggest moment of the loop, in
the single best slot of the week. The new loop's quiet character vignettes
then run through Monday's dead hours, and Monday Prime picks up the new
cast already assembling.

| Day | Beat(s) | Prime-time anchor (18:00–23:00) |
|---|---|---|
| **Mon** | 1 — Introduction<br>2 — The Call | **Arrival at Duke Leto's manor.** The gates, the guards' overheard line, Grovley's uncanny greeting. |
| **Tue** | 3 — Coming Together | **The Party Attack.** Leto murdered, castle burns, Grovley's reveal, escape with the mana worm. |
| **Wed** | 4a — Trials | **First pursuit.** Hunted; the worm is a liability, not a prize. |
| **Thu** | 4b — Trials | **The fork.** Major chat-voted route decision, then the encounter it leads to. |
| **Fri** | 5 — Midpoint Turn | **The sage.** Memory infusion. **Carries macro position 1.** |
| **Sat** | 6 — Darkest Hour | **The loss.** Worst state of the loop — the worm taken, or a cast member down. |
| **Sun** | 7 — Climax<br>8 — Resolution<br>9 — Reset | **Confrontation** (18:00–21:00) → **Resolution** (21:00–22:00) → **THE RESET** (22:00) → **sage handoff + next-loop teaser** (22:00–24:00). |

---

## 5. Loop 1 — Ashiorid, beat by beat

Setting and characters adapted from the existing homebrew campaign. Canon
status is marked per element: **[CANON]** exists in the vault,
**[ADAPTED]** exists but reinterpreted, **[NEW]** invented for the show.

### Beat 1 — Introduction (Mon, Deep Night → Day)

Four intro vignettes, each PC alone in their own life, intercut. Each is on
a personal quest **[CANON]** — seeking family, seeking fortune, hunting a
great evil, seeking a worthier opponent. A courier arrives with an
invitation **[CANON]**:

> *"The Duke Sorensen Leto / Requests the pleasure / Of your company at /
> The Ten Thousandth Anniversary of / The Event / R.S.V.P. to @Ashiorid"*

⚠️ Source inconsistency to resolve before airing: the invitation text says
*Ten Thousandth*, but the timeline and opening docs say **1,000** years.
Pick one. Recommendation: **1,000** (matches the calendar math); the
"ten thousand" figure belongs to the Bahadur's separate Ten Thousand Year
Plan and reads as a copy/paste slip in the source.

An unsigned handwritten note compels each to attend at any cost **[CANON]**.
None of them knows why they can't refuse. **[NEW]** — because the note is
the loop asserting itself; they've done this before.

**Deep Night placement is deliberate**: low-stakes character work in the
lowest-viewership block, and Monday-evening arrivals get it as recap.

### Beat 2 — The Call (Mon, Golden → PRIME)

Convergence on the manor at Cerorossu Lake **[CANON]**. Prime anchor:

- Guards mutter **"one of them"** as the PCs pass (Perception DC 15)
  **[CANON]** — never explained.
- **Grovley** the butler greets each by name: *"Welcome Mr. [name]. It's a
  pleasure to see you again, it's been quite some time."* **[CANON]** —
  despite never having met them.

Both lines are already in canon and both read, unchanged, as loop-bleed.
This is the show's luckiest inheritance: **the existing opening is already
written like a time loop.** No alteration needed. Play them straight.

**Macro seed #1** (position 1 = "first glitch"): Grovley's line is the
audience's first splinter. Nothing more in loop 1. Do not escalate.

### Beat 3 — Coming Together (Tue)

*Afternoon (setup)*: the party is brought to a private room to meet the
ancient hooded figure in white — **the Old Lady** **[CANON]** — who
lectures them on The Event:

> *"A long time ago, the world shook. The cause of which we came to call
> The Event... What once shook the world has slowly passed down into myth...
> I have been tasked with educating you on the subject."* **[CANON]**

This is the *public* version. No loop information. She is the **current
sage** **[ADAPTED]** — canon gives her no name, no immortality, no memory
role, so the sage framing is our layer on a real scene.

*PRIME (payoff)* — **The Party Attack** **[CANON]**, essentially unchanged:

1. An unnamed BBEG and his army storm the castle; his wizard casts a mass
   hold.
2. The BBEG walks to Duke Leto, exchanges words, **stabs him through the
   heart**.
3. *"Burn it down, we will search through the ashes."* Doors barred from
   outside.
4. In a hidden subbasement, **Grovley reveals Leto was father to all of
   them** — sent away to grow up safe from the Begene Sisters' intentions.
5. He hands over the **signet ring**, cut in half by the killing blow.
6. A magic-word-locked vault yields the **mana worm** — which they must
   smuggle out and protect.
7. They escape through a tunnel and watch the castle burn.

Four strangers become a party in the space of one night, through shared
catastrophe. This is why "Coming Together" sits on Tuesday and not later —
canon hands us the bonding event pre-built.

### Beat 4 — Trials & Bonding (Wed–Thu)

Two days, the flex zone. **This is the runtime lever** — trials can be added
or cut without touching the spine.

*Wed PRIME — First pursuit.* Bahadur forces hunt them. The worm drains
ambient magic **[CANON]**, so carrying it makes the party trackable and
weakens their own casters. Excellent pressure: the thing they must protect
is actively hurting them.

*Thu PRIME — The fork.* Chat votes the route. Both branches must converge
by Friday Dawn so the authored Beat 5 fires regardless:
- **Overland** — faster, exposed, a running fight.
- **Underground** — slower, safer from pursuit, but the tunnels are old
  Bahadur workings and the worm reacts to them.

### Beat 5 — Midpoint Turn (Fri PRIME) — carries macro position 1

The party seeks out the Old Lady. Per the two-stage unlock, this is
**opt-in** — they have to actively pursue her; it is not delivered.

She gives the **partial memory infusion** **[NEW]** — canon has her project
nothing, so the mechanic is ours, but the source's memory-projection flavor
supports it. What she conveys in loop 1:

- The Event was not a natural catastrophe. Someone caused it.
- The man who killed their father has been alive far longer than any man
  should be.
- **She has met the party before.** Delivered as if she misspoke, then
  corrected — the audience catches it, the cast doesn't.

That third line is the macro beat. Position 1 is "first glitch," so it stays
a slip, never a revelation.

### Beat 6 — Darkest Hour (Sat PRIME)

The worst state of the loop. Options, pick during production based on how
Wed–Thu actually played:
- The mana worm is taken.
- A PC falls (the wipe-contingency question, still parked — see §9).
- The Old Lady is killed, apparently ending the only knowledge bridge.

The third is the strongest for macro purposes: it makes the audience believe
the information source is gone, which makes the sage handoff at reset land
harder.

### Beat 7 — Climax (Sun 18:00–21:00)

Confrontation with **Sultan Muad'Malik** **[ADAPTED]** — or his BBEG
lieutenant, if you keep his identity hidden for later loops. Canon leaves
the BBEG unnamed and only *implies* the Muad'Malik connection, so both
options are legitimate.

His characterization, canon-exact: immortal since The Event, took a head
injury then that *"over ten thousand years... evolved into a serious mental
illness"*; now rules his people cult-like, toys with necromancy and breeds
abominations **[CANON]**.

**The reconciliation** between canon and our design **[NEW]** — and this is
the doc's key proposal: *he believes the head injury is why his mind is
going.* That belief is canon-true from his point of view. The audience will
eventually learn the injury is not the cause — the loops are. Nothing in the
source is contradicted; a layer is added underneath it.

His dramatic-irony line at the climax: he is not trying to destroy the
world, he is trying to **leave**. The cast hears a madman's raving. The
audience files it away.

### Beat 8 — Resolution (Sun 21:00–22:00)

They win — the worm is secured, the immediate threat broken, at whatever
cost Saturday set. Public relief. It reads as a complete ending. It is not.

### Beat 9 — Loop Reset (Sun 22:00 →)

**The reset choreography** — this is where the CLI presentation earns its
keep, and where a conventional VTuber show couldn't follow:

1. **22:00 sharp** — mid-scene, mid-sentence, the theater pane begins
   dropping characters.
2. Panes desynchronize. The notes pane shows inventory for a character who
   isn't in this scene. An avatar's expression sticks.
3. Progressive corruption of the render — then **every pane clears**.
4. A boot sequence. The world re-initializes.
5. Cold open on the new loop.

No in-fiction explanation is offered in loop 1. The terminal simply breaks
and restarts, and the show acts like nothing happened.

**Sage handoff (22:00–24:00)** — requires both characters present:
- The Old Lady's tenure ends here (loop 1 is her second loop — she is the
  outgoing sage, the "first sage" of the line).
- One PC becomes **sage-designate**, carrying full knowledge forward.
- Everyone else: **hard wipe**.

⚠️ **Blocking decision** — succession mechanism is still unresolved (see
`story_time_loop_concept.md`): does the outgoing sage *choose and groom* a
successor during the week, or does it fall to *whoever is at the
reset-trigger location*? Loop 1 cannot be scripted past this point until
it's picked. The choice changes Beat 5 and Beat 9 materially:
- **Chosen** → the Old Lady spends Fri–Sun evaluating them; her death Sat
  becomes a deliberate handoff, and the show is about inheritance.
- **Happenstance** → position at 22:00 Sunday decides it, which makes the
  Sunday climax's *staging* mechanically load-bearing, and chat could
  plausibly influence who ends up where. More chaotic, more replayable,
  more "Twitch Plays."

Recommendation: **happenstance**, with the Old Lady visibly *trying* to
steer it and failing. Preserves the loop's indifference to intent, gives
chat real stakes, and makes each loop's sage feel earned by circumstance
rather than authored.

---

## 6. What fills the other 112 hours

Improvised block types, all agent-generated within the §2 constraints:

| Type | Block | Notes |
|---|---|---|
| **Camp / watch rotation** | Deep Night | Two characters on watch, in-character conversation. The workhorse. |
| **Dream fragments** | Deep Night | **The best ambient mechanic available.** A sleeping character sees a flash of a previous loop. Deniable as a dream, fills dead hours, and does macro-arc work. In loop 1 use sparingly — one, maybe two, all week. |
| **Chat Q&A** | Deep Night / Day | Characters answer viewer questions in character. Direct engagement, zero plot risk. |
| **Travel narration** | Day | GM describes terrain, weather, wildlife. World texture. |
| **Skill practice / training** | Day | Character mechanics, low stakes. |
| **NPC encounters** | Day | **Twitch-Plays target** — chat drives NPC behavior, never PCs. Protects the scripted spine while giving chat real influence. |
| **Inventory / planning** | Dawn / Day | `open_inventory` → notes pane. |
| **Map review** | Dawn | `display_map` → 20s hold. Also orients new viewers. |
| **Recap** | Dawn | `recap` narration kind. Non-negotiable daily — most viewers missed yesterday. |

---

## 7. Viewer mechanics

- **Chat votes** at authored forks (Thu Prime). Branches must reconverge by
  the next authored beat.
- **Twitch Plays for NPCs only.** Chat never controls PCs — that's what
  keeps the spine intact while the influence stays real.
- **Viewer-count thresholds** (from the original brainstorm): reaching a
  count could spawn support, speed actions, or unlock a scene. Cleanest
  loop-1 use: a threshold unlocks an *extra* dream fragment. Additive only,
  never load-bearing.
- **Sage-designate influence** (if happenstance succession is chosen):
  chat-driven NPC actions could plausibly nudge who's standing where at
  22:00 Sunday. Highest-stakes chat mechanic available, and it costs nothing
  structurally.

---

## 8. Implementation mapping

What this script needs from the platform that doesn't exist yet:

**New primitives** (`config/campaigns/dnd/primitives.yaml`):
- `reset_glitch` — progressive pane corruption → clear → boot. The single
  most important new primitive; the whole reset lands on it.
- `dream_fragment` — distinct visual treatment for loop-bleed (dimmed,
  desaturated, or a different pane).
- `memory_infusion` — the sage's two-stage unlock render.
- `chat_vote` — fork presentation + tally.

**New narration kinds** (`config/campaigns/dnd/narration.yaml`) — currently
only `recap`, `scene_set`, `dialogue`:
- `camp` — improvised character-to-character dialogue
- `dream` — fragment narration
- `infusion` — the sage's memory delivery
- `reset` — the boot sequence's text, if any

**Persona work** (`config/campaigns/dnd/personas.yaml`) — currently ships
`gm` / `thorin` / `sable` as generic demo personas. Loop 1 needs the real
cast: 4 PCs, the GM, Grovley, the Old Lady, Muad'Malik. Candidate PCs exist
in the vault's `Characters/` folder (Helen/Alcinoe, Chadwick, Buffalo, Carl
the Ranger) — **only Helen's details have been verified so far**; the other
three are unread (see `ashiorid_campaign_analysis.md` scope note).

**Scheduling** — nothing today maps beats to wall-clock time. The week grid
in §4 implies a scheduler that fires authored episodes at fixed times and
hands the gaps to improvisation. This is the largest unbuilt piece.

---

## 9. Open questions blocking production

1. **Sage succession** — chosen vs. happenstance. **Blocks Beat 5 and Beat
   9.** Recommendation in §5.
2. **Wipe contingency** — still parked. Becomes urgent if Beat 6 takes the
   "a PC falls" option.
3. **Muad'Malik on screen in loop 1?** — named antagonist now, or unnamed
   BBEG with the reveal saved for a later loop? Recommendation: **unnamed in
   loop 1**; canon leaves him unnamed anyway, and it preserves a macro card.
4. **Invitation: 1,000 or 10,000 years.** Recommendation: 1,000.
5. **Season anchor** — the private answer to what loop 7 reveals. Not
   needed to *air* loop 1, but needed before loop 1 is *written*, so the
   seeds point somewhere.
6. **Timezone target** — the §3 rhythm assumes US evening prime. If the
   audience skews EU/AU the whole grid shifts.
