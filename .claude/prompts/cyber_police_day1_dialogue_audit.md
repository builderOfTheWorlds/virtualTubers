# cyber_police_day1 — dialogue audit (2026-10-02)

Source: message-api GET /replays/cyber_police_day1 (identical to /tmp/cyber_police_day1.json,
built 2026-10-01 03:46) + `docker logs virtualtubers-worker-roundtable-1` (ANSI-stripped `♪ ` lines).
Deterministic text analysis only. Durations use 150 wpm.

## Live episode (v1 builder)
- 4,600 lines, 107,332 words, 11.93 h. Unique lines 1,282 → ratio 0.279; 65 distinct lines repeat, 3,318 extra airings.
- Spine (7 scenes, 1,605 words, ~12.4 min) looped 58×: 58 lines × 58 airings = 3,306 of 3,318 repeats (99.6%).
  Repeat gap avg 12.8 min, median 12.3 min, spine min 11.4 min. First repeat at 0.21 h.
- Ambient: only ~174 of 1,415 takes used (3 per loop) — 1,236 lines, 1,224 distinct. 7 lines repeat (12 airings),
  all the same line appearing in different takes ("Observer:" ×6, "Morning, everyone." ×3); min gap ~0 (same take).
- Near-dup (3-gram Jaccard/difflib ≥ 0.85): 6 pairs, all ambient ("Alright, let's get the briefing started" vs "Captain Agar: Alright, ...").
- Aired as of the 05:15 UTC log: 3,320/4,600 lines (≈8.6 h in), 42 spine loops, 953 unique of 3,320.

## Other quality issues (whole episode / aired so far)
- Generated GM-narration beats voiced by the Captain's seat (tuber_0): 223 / 146. Most are "Name: line" for another character.
- Name label read aloud: 230 / 147; label names someone other than the voice: 186 / 118 ("Marcus Thorn: ..." in the Captain's voice).
- Stage directions read aloud "(sighs)", "[...]": 221 / 163. "||emotion:" residue spoken: 21 / 13.
- Silent Observer "lines" (label with empty/stage text, voiced by tuber_0): 10 / 8. Bare labels "DO:", "TM-T:": 11 / 9.
- Missing terminal punctuation (truncated-looking): 99 / 60. Refusal / "as an AI": 0. Foreign-pack leak ("Ashiorid"): 1 aired, 15 in pool.
- Pool level (audit_cyber_police_takes.py): 1,828 label-in-text beats, 1,769 narration beats (all speaker=captain),
  1,943 stage directions, 122 emotion residues, 72 observer labels, 98 lines in >1 take.

## Root causes
1. build_events looped the spine until the word target was reached; ambient injection was per spine scene
   (every 2), so the spine dominated (93k of 107k words) and 88% of the take pool was never used.
2. The generator (improviser.generate_scene) emits "Name: line" with initials/aliases it can't resolve
   (DO, TMB-L, HV...). Those fall to GM narration with speaker=captain, and the v1 builder voiced them in the Captain seat.
3. No dedupe of identical lines across takes, and no stage-direction or emotion-tag stripping in the builder.

## Fix: builder v2 (build_cyber_police_full_episode.py)
One spine pass spread across the day. Each take used at most once. Label resolution and cleaning.
RepeatGuard (exact + near-dup, 12 h window) plus a final assert_no_repeats.
PoolTooSmall → exit 2 unless --allow-short. Tests: tests/test_build_cyber_police_full_episode.py.

Candidate: .claude/prompts/out/cyber_police_day1_candidate.json (not uploaded). 8,097 lines, 95,698 words,
10.63 h, unique ratio 1.000, no repeats, 0 near-dup pairs. Validator PASS. Short of 12 h by 11,450 words (~135 takes).
