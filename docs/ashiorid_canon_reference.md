# Ashiorid — Consolidated Canon Reference

> **Status**: Full vault sweep complete (2026-08-02). ~100 real content files
> read across 6 agents; 1 folder (meta/index) incomplete — see §11.
> **Purpose**: the usable canon, consolidated. Provenance and reliability
> analysis lives in [`ashiorid_campaign_analysis.md`](ashiorid_campaign_analysis.md);
> **read that file's reliability warning before trusting any single entry
> here.** Show design built on this: [`weekly_loop_script.md`](weekly_loop_script.md),
> [`loop_01_hour_by_hour.md`](loop_01_hour_by_hour.md),
> [`season_01_arc.md`](season_01_arc.md).
>
> **Source vault**: `C:\Users\frogg\OneDrive\Documents\codeProjects\ashioridCampaign\DnD Campaign\`

---

## 0. ⚠️ SAFETY — ACT ON THIS BEFORE BUILDING ANY PIPELINE

**`Resources/Cautionary Tales/SackWhores.md`** is not folklore. It is an
archived Reddit "D&D horror story" containing graphic child sexual
exploitation and violence against children. **If any retrieval, RAG, or
context-loading step touches `Resources/`, this file is a live hazard** — it
could reach an LLM context and then the stream. **Quarantine or delete it.**

Also filter before use:
- **`Resources/Character Traits Reference.md`** — ~180 traits scraped from
  D&D Wiki, including `Racist`, `Sexist`, `Xenophobic`, `Dat Booty Doe`,
  `Masochist`. Useful as a controlled vocabulary **after filtering**.
- **`Resources/Humor/Tortilla Sanchez.md`** — mad-libs output, genital
  jokes, set in "Yellow Mexico", zero campaign connection. Unusable.
- Scattered stat-block leftovers like Kayla's *"Hotness rating of 19"*.

---

## 1. TONE — the fork you have to call

The show docs written so far are **tonally grim**. The source campaign is
**broad, bawdy comedy**. Evidence:

- A PC named **Chadwick Von Donner Schwanz** ("thunder-cock"), with
  **commissioned heraldry** for the joke, and a photo of him wearing a
  necklace of garlic and chili peppers.
- His inventory: *"Eyeballs of a cardinal corpse, **seems unmagical and
  tasty**"*; a spoon collection containing one **lethal murder-spoon**.
- **The Rob Boss** — a Bob Ross parody who hires you to steal a painting.
- **WerePomeranian** — the werewolf is a deaf old lady's yappy dog, so the
  party must shout every line.
- **The Mage Hole** — half a mage rockets out of a pit, dying words:
  *"Find the other half... I hid it... in my... asshole."*
- Real in-world tabloid: *"The Emperor is a bugbear in disguise!"* and
  *"The second Elf war was an inside job, Dragon's breath can't melt steel
  beams!"*

**But** the same vault contains the Bogdan press driving a prince to
suicide, a priestess cursed into a beast who can never tell the truth, and
an immortal forgotten beneath his own monument. **The register is
Discworld — jokes and real cruelty in the same room.** Recommend matching
that rather than picking one.

---

## 2. THE PLAYER CHARACTERS

### ⚠️ Name confusion — resolve before writing personas

`Party Kills and XP Tracking.md` uses Character/Player columns:

| Character | Player |
|---|---|
| **Alcione** | Helen |
| **Vance** | Carl |
| **Buffalo** | Cody |
| Branch | Roberto |
| Dio | Vermond |
| Vigil | Phil |

**"Helen" and "Carl" are player names.** The vault named two files after
players, then rationalized it ("Real Name: Alcinoe, goes by Helen").
Spelling drifts **Alcinoe** vs **Alcione**.

**Three PCs with XP history but no character files**: Branch, Dio, Vigil.
Free named cast slots.

### ⚠️ The backstory collision

**Buffalo and Carl both have named, specific birth parents.** Buffalo's are
half-orc paladins Lelu and Suny; Carl's were elf-slavers. This is **flatly
incompatible** with "Leto fathered all of them." The Leto retcon was layered
on top and **never reconciled inside the character files.** You must pick a
resolution.

### BUFFALO (player: Cody) — Half-Orc Paladin, Oath of Vengeance, NG
6'1", 215 lbs, light grey-tan skin, **whitish grey mohawk**, tusks ~2"
protruding outside his lips even when closed. STR 14 DEX 11 **CON 9** INT 15
WIS 16 CHA 15 — *smart and perceptive, physically fragile. Not a meathead.*

Personality, the entire written section:
> Honorable and devoted to his oath · **Quick to anger, especially with
> people who talk too much** · Values peace and quiet · Does not tolerate
> challenges to his authority · **Frequent Problem: Always seems to get
> himself lost**

Backstory: age 5, a freak hurricane hits Henderson; parents spot the orc
warlock **Kibawa** and chase him; swept away by flash flood, **bodies never
found**. Adopted by **Henry III** of the paladin order **The Crimson
Guard**. Henry III's son **Henry IV**, jealous, turns to evil magic — and
Buffalo *"does everything he can to help Henry IV become a great paladin"*
anyway. The confrontation is written as dialogue:
> **Buffalo:** "Innocent people died in that storm!"
> **Henry IV:** "Everyone that died deserved to."

Kibawa's order — *"Kill him now and prove your loyalty"* — and Henry IV
murders his own father. Buffalo finds the corpse *"on a sacrifice table, all
his blood drained out, surrounded by demonic symbols."* Henry IV steals the
heirloom shield **Bacoo** at the funeral.

**The dramatic irony**: *"Buffalo does **not know** that Henry IV is allied
with Kibawa. He believes he's hunting them separately."*

**Signature move** (from the one written scene): escalate silently, get
laughed at, disarm two thugs, **slam the taken dagger into the bar** in
front of the gangster — *then* succeed. Fits "hates people who talk too
much" perfectly.

**Show note**: Buffalo attended **2 of 8 sessions** and ended at 215 XP vs
the party's 3,165. He is canonically **the guy who keeps missing things** —
which dovetails with "always gets lost."

### CARL / VANCE — Human Ranger, 32, 5'11", *"worn face from years of wandering"*
Thinnest written personality of the three (all abstract virtue, no voice) —
but the richest situation.

Born to **elf-slavers who killed elves for trophies — "ears and scalps."**
At 6-7, wood elves wipe out the band, find the boy **taking care of an
animal**, spare him for it — and kidnap him. Raised in **Nafsari**, an
elven treehouse city, tribe **Shiye-Taarii**. *"The elves **never trusted
Carl**. Gave him menial jobs. Never treated him well, but never abused him.
When he left at age 16, most elves didn't care where he went."*

**The guilt engine**: at ~16 he gave directions to a "lost elf" who was a
**Drow in disguise**. Weeks later a Drow army sacked Nafsari and killed his
foster parents. His best friend **Arun** blames him *"irrationally (though
he doesn't know Carl is actually responsible)."*

**The ticking clock**: he carries a vial of the **Elixir of Life** from a
fountain behind a waterfall. Drow want it to revive their Balor leader. He
filled the fountain in with dirt; refill estimated at **15 years**. **9 have
passed — ~6 remain.**

Runs an underground railroad for freed elf slaves. **Never been caught.**
Nine years of asking humans for help: *"Mostly laughed at. Gotten into
brawls over it. Thrown out of some towns."* Has a **direwolf** he abandoned
as a boy that has been following him ever since.

### HELEN / ALCINOE — Human Sorcerer (fire). **The Leto thread.**
*(Index calls her a Runeseeker — the index is wrong; that's INT-based, her
list is CHA fire evocation.)*

Personality — **the sharpest writing in the vault**:
> **Trained to be patient. Natural character is energetic.**
> **Ideal:** treat everyone as an equal
> **Flaws:** a bit naive · stubborn · **cold**

*A person constantly suppressing her own tempo.*

Raised in a **pocket universe**, city of **Aella** — presented as a happy
nature-focused village, secretly a **matriarchy**, ruled by a Council of
Families; her "mother" outranks her "father"; **she was being raised to be a
leader.** Technology *"past the nuclear era"* blended with magic.

Magical schooling at **Penthesilea** (~200 students, strict). Children cast
from toddlerhood; entering school after mastering conjuration is described
as **"a magical puberty."** *"Helen's family specialized in fire-based
magics."*

Two beautiful specifics: **favorite memory, age 4** — a festival where
everyone was eating and playing with magic. **The defining moment** — she
and other children snuck into books *"off-limits to children"* and found one
about the outside world. **That book is why she's in the story.**

**All her possessions are enchanted with a perception filter** — *"most
people wouldn't be able to look at them or notice their worth."* She carries
priceless things nobody can perceive.

**The truth**: Leto III's daughter via the Begene Sisters' **40-generation,
1000-year** program. Leto liberated his children and scattered them — **one
girl could not be found. That girl was Helen.** The Sisters *"kept her on a
very short leash... she was the last of their long line."* Then the
pocket-universe people made *"a special exception"* to abduct her.

DM's secret handout, verbatim: **"You don't know your real family"**

**Her spells are custom and made for a terminal show:**
- **Fiery Eyes** — *"Your eyes glow with unnatural fire... **You may choose
  what color they burn.**"* Stare 3 rounds → combustibles ignite. Eye
  contact within 10 ft → save or incapacitated, *"fascinated by the
  unearthly light."* Gives disadvantage on saves vs her charms.
- **Handcandle** — flame in her palm, **swap red (hot, damages, cooks) to
  white (bright, cool, pure light)** as a bonus action. She can be a walking
  lamp.
- **Flashpuff** — blinding flash + colored smoke; **"You can sculpt the
  smoke into a simple, two-dimensional image."** Built-in visual aid.
- **Dancing Fire** — a 12-ft leaping arc travelling 10 ft/round, no save, no
  concentration.

### CHADWICK VON DONNER SCHWANZ — **not in this campaign**
Different system (**Nimble 2.0.3**, not 5e), different setting (Glassstaff
and Leilon are *Lost Mine of Phandelver*), dated ~2019, and **appears in
zero other vault files**. Making him a Leto sibling is invention.

Fourth child of the **Earl of Idra** — titled, entitled, inherits nothing.
Left at 29 for *"treasure, adventure, gold, and most importantly women."*
*"hiring companions to take with him, **few return**"* — and it bothers him
(*"growing weary from the fallen companions"*). Sired **many children, some
to married women**. Fleeing **Glassstaff**, a wronged husband.

Appearance: 6'9", lean muscular, **voluminous pompadour**, short beard,
*"his shirt has **multiple collars**"*, filthy from a dungeon, plain wooden
staff. Art shows him in a **garlic-and-chili necklace** (an in-joke from
play — keep it).

Build: **Berserker 4, Path of the Red Mist**, STR 3 DEX 2 INT 0 **WIL −1**,
47 HP, **Armor 0**, beats people with a wizard's staff. Was a **Mage**
before — same inventory, rebuilt — and kept one spell, `Savage Arsenal:
Magic Missile` *(Homebrew)*. **Enduring Rage**: *"While Dying: Rage
automatically for free... max 2 actions"* — **stronger as he bleeds out.**
**Bone-Crafter** (DM-granted): *"craft equipment of strangely high quality
out of corpses. 1 corpse a 1d6 weapon, 5 corpses a shield, 10 corpses armor
+1 all resists."*

**Only recorded line**: *"Sanguis, not everything is an enemy."* — written
in advance under a heading **"Things to say."** He pre-writes his own
zingers. **Sanguis** and **Sazza** ("demon lady we found in a graveyard")
appear nowhere else in the vault.

Structural echo worth using: **he is a serial absentee father in a campaign
whose entire plot is a serial absentee father's scattered children.**

---

## 3. THE FOUR OPENING MOTIVATIONS (canon, `Campaign Opening`)

> One man seeks **family** · One man seeks a **richer future** · One man
> seeks a **great evil** · One man seeks a **tougher opponent**

Maps to: family→Helen, richer future→Chadwick, great evil→Buffalo (hunting
Kibawa). **"Tougher opponent" fits none of the documented four — an
unclaimed slot.**

---

## 4. NPCs — the best material

**YLVA** *(source-verified)* — human, **14**, eyes *"bright green that
almost radiates light."* **Foresight a few seconds ahead**, involuntary and
traumatic: *"her foresight sometimes shows her terrible futures."* Canon
scripts her voice: *"Wait! Something's about to—"*. Both parents presumed
dead; survived alone in the Frozen Fortress.

**The hook**, verbatim: *"If the party successfully gets Ylva to safety, her
lineage will survive to the far future. A very small amount of people will
carry her foresight, and it will be a **selected genetic trait by the
breeding program**."* → **The setting contains a machine that manufactures
prophets, and it is still running.** She is a product of the same program as
Helen, Buffalo and Carl.

**AZRA** *(source-verified)* — Bahadur assassin, *"one of the first through
the portal."* Skin **artificially lightened almost pure white** for makeup
disguise; **faintly glowing blue eyes** — and **she doesn't know why**, she
*"just accepts it as fact of her caste."* Fatalistic: *"already a dead
woman."* Poison sewn into her suit; must be physically stopped from suicide.
Two deliberately-unexplained details: **surgical scars** on abdomen and back
(*"Leave this ambiguous"*), and a **metal flower, slightly lavender, that
smells like lilac.** **Shares no language with the party** — magical
translation required. Bahadur are *"desperate but not evil."*

**DROKKI REDGEM FINNSSON** *(source-verified)* — dwarf **Runeseeker**,
master smith of Vabokedos. **Immortality mechanism is literally just
"survived the Frozen Wastes"** — no ritual, unexplained by design. Grows
restless after *"a long time of not dying"*, vanishes. **Vabokedos erects a
monument to him while he is still alive**, imprisoned under Uksossnong. *The
statue outlasts the memory of the man.* His **sentient greataxe** talks, is
indestructible, teleports to hand — and **prevents short rests within 300
feet.**

**GROVLEY** ⚠️ — **95% AI-generated.** Source file was one word: `Butler`.
Real canon only: he is Leto's butler; he reveals the PCs are siblings from
the Begene Sisters' experiment; he reveals they hold an object preventing
dimensional invasions; he helps them escape. His greeting line *is* canon
(it's in `Campaign Opening`, a separate file), and the DM's own note asks:
*"Why does Grovley say 'it's been quite some time' when greeting characters
he shouldn't know?"*

**THE ROB BOSS** — Bob Ross parody. Windowless brick building, single blue
door, hallway of colored doors, white door with two armed guards. Inside: a
very old man with *"strangely spherical hair"*, a *"small woodland
creature"* chirping on his shoulder. Ignores you for minutes, then *"I think
this one is finished"*, turns: **"Hello, I'm The Rob Boss, and I'd like you
to steal a painting for me."**

**PAMELA, THE SHEWOLF OF IDRA** — priestess who turned assassin to save the
clear-cut forests, killed the woodcutters' owners, prayed to **Mielikki**
for forgiveness — *"The goddess was appalled at what had been done in her
name, so she cursed the priestess, transforming her into a hideous beast."*
She hid for months speaking only through a closed door. Turned to Mielikki's
enemy **Malar**, who restored her beauty at a price: lycanthropy, and **she
is compelled to lie — she can never tell the truth.** Now runs a temple that
appears to serve Mielikki and secretly serves Malar, hunting Mielikki's
faithful. *A compulsive-liar antagonist is a hard constraint a generator can
actually enforce.*
Innocents in her temple: **Nicole**, 18, lawful good, naive, who prays
*"daily for a well growing forest to support her family of lumberjacks"* —
praying, unknowingly, in a temple to the thing that eats forests.

---

## 5. THE WORLD

### Magic, and why it's rare
- **Energy powers magic, food production, and survival** — energy scarcity
  is a **famine mechanic**, not just a magic one.
- **Moonwells** are upstream sources; *"Energy follows water... Downstream =
  further from source = less energy. Controlling upstream = controlling
  energy supply."* ⚠️ **No moonwell has ever been described, named, located,
  or visited.** What they *are* is an explicit open question.
- The mechanism: *"**The Event caused terrible damage to Ashiorid's energy
  ecosystem, more so than was originally planned.** This caused magic to
  become a rarity."* → **The Event was someone's plan that went wrong.**
- **Mana worms / magic dust cartel**: *"The less magic that is available in
  the world, the more the population needs the dust. The more that the
  people need the dust, the more the group profits."* And: *"The mana worms
  don't just suck magic out of magic users—they take it from the world
  itself. They are the c[ause]—"* **← the sentence is truncated mid-word in
  the source.**

**How rare, in practice** (Malmont, pop 5,349): 2 magic shops · **134 clergy
vs 5 priests** · highest wizard level 8 · **4 druids total** · **4,420
level-1 commoners.** Life expectancy **52**. Age pyramid **48% children /
39% adults / 13% elders**.
→ *A world of children, almost nobody over 55, where magic is a rumor.*
The Losira necromancy scene lands exactly here: three strangers resurrect a
lizard **in a crowded bar** and *"Some thought it was merely a magic trick /
Others were too drunk to notice."* Only the druid understood.

### The desert — the whole file is four lines, and it's huge
> **The removal of magic causes deserts to form and expand.**
> **In Ashiorid:** The Outrageous Desert has already begun to grow.
> **In Ord:** The desert has taken over most of the planet.

Growing since **Year 110 — 420 years and still advancing.** It is the
**visible, public, deniable symptom** of magic loss — the piece of the
conspiracy an ordinary farmer can see without knowing what it means.

**Ord is a planet that is now all desert. Ashiorid has a desert that grows
every year. Anyone who connects those two facts has worked out the ending.**

### The calendar counts down, then up
Years run **690 → 0**, then **0 → 310** (now). **Year 0 = "Portal Closed"**,
the end of the Daemon Invasion. The calendar was **re-founded at the
portal's closure**. Scribes would say *"the year 490 before"* vs *"the year
110 of the Peace."*

### Nobody knows what The Event was — and canon says so
The Old Lady, verbatim:
> *"A long time ago, the world shook. The cause of which we came to call The
> Event. Have any of you heard of this? **I'd be very surprised if you
> had.**"*
> *"**What once shook the world has slowly passed down into myth. Few people
> even remember the myth anymore, fewer know that it was real.**"*

→ **The Bahadur are invisible in daily life.** No popular awareness of Ord,
the Event, or the invaders. They are a *hidden power*. And yet **the
nobility are throwing a thousandth-anniversary party for an apocalypse they
don't understand** — outstanding ambient material.

### History you didn't have
**Astrior** appears Y680 (nothing else in the vault mentions it) ·
**Diokles Empire** falls Y610 · **First Elf War** Y560-530 · **Thirty Years
Heatwave** Y570 · **Thirty Years of Winter** Y510 · **Frozen Fortress and
True North lost** Y490 · **Aryeh Epidemic** Y420, cured by **Zinoviy** Y400
· **Daemon Invasion** Y320 · **Flee to Argyre** Y250 *(the current continent
is not the ancestral homeland — completely undeveloped)* · **Carnage Wall**
built by **Donaldo** Y180 · **Birthing Project ended** Y150 · **Wilton
becomes a Lich** Y40 *(is he still active after 350 years?)* · **First
Emperor Gordianus** Y10 · **Anthemic City destroyed** Y90, cause unknown ·
**BLACK POWDER INVENTED Y200** — guns are canon and 110 years old.

**Cyclical extreme weather every ~300 years** is flagged by the vault's own
notes as an open question — free in-world doom prophecy.

---

## 6. POLITICS — a working numeric engine

**A verified 13×13 symmetric diplomacy matrix**, scored 1–10, arithmetically
consistent (I checked the published averages). Origin documented in a 2016
note: the author rolled **1d10 per relationship** and colour-coded the grid.
Scale: 1–3 hostile · 4–6 neutral · 7–10 allied. Average hovers 4–5 —
*"a tense, unstable political landscape."*

Lowest: **Vespasino 3.75**. Highest: **Lucilus 5.75**.
Blocs: **West** (Bogdan/Elden/Lysander) · **East** (Casimiro/Vespasino/
Katashi) · **South** (Govannon/Lucilus/Hieronymus/Siguard) · **Unaligned**
(Edvard/Kori/**Loke**).

**Loke is formally neutral, informally connected everywhere** — 10/10 with
Bogdan, 9 with Siguard — and holds **444M gp and 159,433 people, ~2.8× the
wealth of all other quantified factions combined**, 88% of it in one city,
**High Port** (pop 78,616). *Venice or Genoa at their height.*

### The live grievances (all canon, all dramatizable)

**The Bogdan propaganda machine — the single most usable mechanic in the vault:**
> *"The Bogdan are master paper makers... They also have **the only newspaper
> in the Empire**. The production of which is kept as a closely guarded
> secret. **They invented a printing press, and hide it within a pocket
> dimension.** The Bogdan use this paper to influence the masses with fascist
> propaganda."*

**What they did with it**: Casimiro publicly accused them of fascism. Bogdan
*"made [them] appear as pedophiles. They produced false evidence... with the
help of **Hieronymus drugs**. The Casimiro Prince **Blake Casimiro** was
accused in grand court by a drugged girl, no older than six years old.*
*While there wasn't enough evidence for an indictment, there was enough for
the public to believe it... after months of constant harassment, **Blake
Casimiro took his own life.**"* **The two houses have been at war since.**

**The Lysander poisoning**: Baronet **Roxana Lysander**'s husband **Gaios**
died of poison she believes came from Hieronymus tea. She **accused them
publicly in front of the Emperor**, had no proof, and he did nothing — so
she went around him via her brother in Lucilus to strangle Hieronymus trade
routes. *"Baronet Roxana Lysander **despises the Emperor** for his
inaction."* She is also searching for her **missing daughter Zoe**.

**The slavery split**: Loke ignores the slave trade. Roxana *"will publicly
whip and brand any person caught trading slaves."* Vespasino confiscate the
slave, fine the trader *"a paltry sum"* and release him — **"The slaves are
never heard of from again."**

**Emperor Secus** keeps *"several princes and princesses from the 14
factions"* as **political hostages** in Vabokedos. Four decades of peace,
now crumbling. **The murderer of King Fredrick Lysander is never named
anywhere** — the biggest free hook in the political layer.

Travel flavour, the best sentence in the vault for it:
> *"Small skirmishes begin to happen along boarder towns. Brigands start to
> become more common along main roads, **the Carnage Wall keeps losing men,
> and are not being replinished**, the realm comes to the brink of war."*

---

## 7. PLACES WITH ACTUAL CHARACTER

**VABOKEDOS** — dwarven city carved into a mountain, five clans locked in
**ritualized theft**:
> **"We're not stealing, just getting our stuff back."**
> *"Dwarves never get mad when another dwarf steals a dwarven heirloom -
> they know it's in the hands of another dwarf."*

Power shifts as heirlooms change hands. Top prize: **weapons forged by
Drokki**. Dwarves **hoard** stolen items rather than sell. *"Not welcoming
to guests they don't know / Very apprehensive (always expecting theft)."*
Each clan has **different beard styles and skin colours.**
The drug **Skuma** *(called **Fooma** in another file — inconsistency)*: a
crystal that, crushed and applied to skin, **grows thick luxurious hair on
that spot.** Run by the **Kato** brothers — one refines inside the city, one
farms outside — who don't betray each other out of *"genuine respect."*

**HENDERSON** — desert mining town, best atmosphere writing in the vault:
*"Constant heat / Water is precious / Sand everywhere / Night: freezing cold
/ Dust storms"* and *"**Metal sounds: hammers, forges / Furnace heat adds to
desert heat**."* Cannot grow food — **entirely caravan-dependent**; water is
mountain snowmelt. Famous for **orcish armour** and finest-quality
commissions for the Paladin Order. Founded by **Henry the First Paladin**, a
half-orc — *Henderson = Henry's town*. Buffalo's home.

**LOSIRA** — **United Shields Military Academy**, motto **"United We
Phalanx."** **Commander Byron "Shield Crusher"**, ~60, who *"smashed through
an enemy shield with his warhammer, killing his opponent in a single
devastating blow. However, during that same battle... **his guts were
smashed out by a mace**."* Can't move fast or bend; still enormously strong;
teaches because the wound ended his field service.
**The Wooden Hat** pub — *"According to local legend, the pub was founded by
**a tree who wore a hat crafted of the finest wood**."*

**SARAH'S SIMPLE INN** — **promote to permanent recurring set.** Thunderous
footsteps, rumbling floor, a hulking mass looming out of the dark, maw
opening — then, in a southern drawl: **"Howdy y'all, welcome to Sarah's Inn.
What can I get y'all on this fine evening?"** Full priced menu (Sarah's
Famous Stew 2sp, ale 4cp, **Sarah's Special Brew 1gp — "Strongest drink in
the land!"**), four rumour hooks, and a combat variant where **Sarah fights
with a rolling pin.** The Notice Board file already cross-references it.
*One location that composes board-reading + busking + gambling + rumours +
shopping.*

**THE CARNAGE WALL** — the entire file, four unpunctuated lines:
> *Buit by mages when the world was young / Magic was used to uplift the
> ground to form a massive wall to protect their civilization from the goblin
> hordes of the West / The wall still stands and is still manned today /
> Excellent location for a siege*

Zero detail, **strongest ASCII concept in the vault.**

---

## 8. ITEMS & CLASSES WORTH BUILDING ON

**TREANT'S FALL** — fully realized. *"A wooden greataxe that pulses with
life energy. **Every morning, a fresh leaf sprouts from its handle.**"*
Made by treants **from their own bodies** after **St. Nicholas** felled
their forest. Inscribed: **"Nicholas destroyed our forest, but the trees
shall rise again."**
**Any biological enemy killed is transformed into a pine tree**, height
matching the victim, *"**The enemy's equipment adorns the tree as ornaments
(like a Christmas tree)**"* — *"a dark inversion of the Christmas tree
tradition."* Harvested leaves plant into **controllable treants** in 1d4
turns. One leaf per morning.
→ **The map itself records the body count.** A daily leaf counter is a
perfect persistent pane stat.

**THE RUNESEEKER** — the real homebrew, and the most text-native concept
available. **Runestones**: *"small stones with magical symbols engraved on
them... written in **lost runic characters that represent magical energies
directly (not related to any specific language)**."* A magic system whose
atoms are **engraved glyphs in a script that isn't a language** — that
renders natively in a terminal.
**The weapon wakes up**: at 3rd level the bonded weapon becomes **sentient**
— communicates by transmitting emotions, 30 ft blindsight, ability scores,
personality traits, and *"**the weapon remembers everything from its
creation**."* At 7th, telepathy. **At 10th, it speaks two languages.**
→ **A second AI voice with a built-in progression arc**, that starts mute
and learns to talk.
Example quirk: *"Other creatures can't take short rests within 300 feet of
you while attuned."*

**THE BLOOD-IRON / LEAD PLAGUE** *(file is a raw Gemini chat transcript —
no statblock, but the worldbuilding is real)*: *"one of the bad guys has
been poisoning the entire population with lead"* for two generations. 12 per
1,000 show **Burton's lines** (blue-purple gums) — implying **nearly 100% of
the population is heavily poisoned**, invisibly, because no medieval healer
checks gums. Two blades: an ancient demon-forged one (~400 bodies, mirror
chrome) and a modern goblin-corpse one (~4,600 bodies) whose **Damascus
patterning is bone-ash**, wickedly sharp and **likely to shatter on heavy
armour**. Open question in the file: *"Is the sword sentient because the lead
poisoning caused neurological decay in the victims, and that 'madness' is now
trapped in the metal?"*

---

## 9. AMBIENT ENGINES — the answer to the 88-hour problem

**1. The "DREAM STATE"** *(canon, invented by this table)* — absent players'
characters were run at **10% XP** in a "dream state": *"partially unconscious
or in alternate reality. Allows players to participate when unable to attend."*
→ **An in-fiction, canon-legal mechanism for present-but-not-present.**
Solves benching, reduced presence, and alternate-reality segments — and
rhymes perfectly with the loop premise. **The single most reusable find.**

**2. The Amulet of Wonder's six activation conditions** — take a life · fall
to 0 HP · make a personal sacrifice · steal greatly · acquire great wealth ·
lose everything. A **progress bar advanced by character experience, not plot
events** → *ambient hours can advance it.* This is the mechanism that makes
low-stakes hours matter. (Also ships **12 riddles in 12 languages** and ~60
named puzzle rooms.)

**3. The Downtime three-check structure** — Crime, Gambling and Pit Fighting
share one skeleton: **three checks, four outcome tiers.** Three beats plus a
resolution, with scripted failure states and **self-selected difficulty**
(a built-in greed-vs-caution argument). Pit Fighting is explicitly
**non-lethal** — combat spectacle at zero risk to the cast.

**4. The Bogdan newspaper** — a secret monopoly press in a world of 13
feuding houses. **A broadcast format handed to you by the source.** Anything
the show wants to "cover" can be covered — or contested — by the Bogdan
paper.

**5. The Notice Board generator** — a real two-layer template spec with a
variable dictionary and a stated composition ratio (*"2-3 tabloids, 1-2 real
quests, some services"*). ~30% filled; the empty categories are the easy
ones. Contains an **escalation mechanic**: *"The missing child posts can
gradually escalate in frequency, creating an investigative thread for
observant players."* → **ambient time accumulating into plot for free.**

**6. The diplomacy matrix + Malmont census** — numeric, non-contradicting,
infinitely extensible background politics and local colour. Malmont's census
is a directory listing: **35 shoemakers, 22 furriers, 16 barbers, 14
taverns but only 3 inns, 5 chicken butchers as a trade distinct from 5
butchers, fewer than 1 bookseller.**

**7. Serial ambient devices** — `Mystery Boy and Poison Ring` is *"the same
eerie thing happens every night for a week"*: a silent, untouchable boy
sits at the campfire one hour nightly, then vanishes; on the last night
someone wakes wearing a ring holding a poison that kills the target **and
the poisoner**. Steal the *shape*, not just the quest.

---

## 10. WHAT DOES NOT EXIST — guardrails for the GM agent

- **No pantheon.** No gods, churches, holidays, or religious calendar —
  despite clergy being the largest professional class in a town. Only
  **Mielikki and Malar**, borrowed from Forgotten Realms.
- **No description of a moonwell.** Ever.
- **No description of The Event.** File is 0 bytes.
- **No physical description of a mana worm.** None. Anywhere.
- **No floor plan for Duke Leto's manor.** The subbasement, vault and tunnel
  — the load-bearing set for the show's opening 30 hours — total **about six
  sentences**. The best sensory writing is in `Campaign Opening`, not
  `Locations/`.
- **Cerorossu Lake is a name with no description** and no file.
- **No in-world folklore.** No campfire tales, legends, songs, or
  superstitions. **The largest ambient category is completely empty.** The
  closest thing is the Amulet's curse-poem — proof the author *can* write in
  that register.
- **No travel times, road names, or postal system.**
- **Every `Maps/` file is a catalogue of images that don't exist** in the
  repo. Only **one** map image survives, in `unsorted/`. The castle room
  list (10 rooms) and the note that the manor is **based on Harlech Castle**
  are the only renderable assets.
- **`unsorted/` is a graveyard** — 11 of 13 files are 0 bytes.
- **No leader names for 12 of 13 factions**; no wealth/population for 8 of 13.
- **No languages named**, beyond "gnomish" in passing.

**Protect the three vacuums** (The Event, the moonwells, the pantheon):
instruct the GM agent to keep them mysterious and never explain them on air.
Canon supports this directly — nobody in-world knows either.

---

## 11. KNOWN CONTRADICTIONS — pick a side before airing

1. **Emperor has two names**: **Gerontius Sigurd** (2016 originals) vs
   **Secus** (2025 imports). "Siguard" is separately one of the 13 vassal
   factions.
2. **13 vs 14 factions.**
3. **Two inciting murders**: Duke Leto III (2016 note) vs King Fredrick
   Lysander, Nov 309 (timeline). Possibly the same crisis from two drafts.
4. **Roxana** is Gaios's widow in one file, Fredrick's daughter in another;
   **King** Fredrick vs **Baronet** Roxana.
5. **Invitation: "Ten Thousandth Anniversary"** vs 1,000 years everywhere
   else. *(Recommendation stands: use 1,000 — and see
   [`season_01_arc.md`](season_01_arc.md), where Loop 7 turns this into a
   plot point.)*
6. **"Four young men"** in the opening vs Helen being a woman.
7. **Skuma vs Fooma** — same dwarven drug, two names.
8. **Magic abundant vs scarce** — `Bahadur Race.md` says *"In Ashiorid:
   Magic is abundant"*, contradicting post-Event scarcity. Probably means
   *relative to Ord*.
9. **Helen: Sorcerer** (her file) vs **Runeseeker** (index). Index is wrong.
10. **Treant's Fall as "Buffalo's weapon"** — index-only claim; the item file
    never mentions him.
11. **Drokki** listed as a PC in the index, NPC everywhere else.
12. **Buffalo's and Carl's named birth parents** vs the Leto premise (§2).

---

## 12. NOT YET READ

The meta/index agent was killed by the session limit mid-run. Still
unswept: **`Content Discovery Summary.md`**, the root **`README.md`**, a
full **dangling-wikilink extraction** from `Campaign Index.md` (unwritten
ideas = free creative real estate), and `Resources/File Reference Index.md`.

That last one is known to catalogue **files that were never imported** and
still sit at `C:\Users\matt\PycharmProjects\ashiorid\lore\DnD Campaign\` —
including a **`Mass NPC Generator.xlsx`** and **`minor_curses.pdf`**, both
directly relevant to the ambient problem, plus the real **D&D Shop
Catalog `.docx`** (the vault's copy has no prices). **Worth recovering that
directory if the old machine is reachable.**
