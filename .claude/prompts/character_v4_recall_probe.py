#!/usr/bin/env python3
"""
character_v4_recall_probe.py

Checks that plan v4 §7 trajectory matching (Smith-Waterman over cosine
similarity of beat embeddings) can tell a lead-up that is replaying from one
that isn't, using real nomic-embed-text vectors from the gx10 Ollama. The
output calibrates the default `bias`, `gap` and threshold values in
config/character.yaml.

    python .claude/prompts/character_v4_recall_probe.py [--base-url http://192.168.1.23:11434]

No DB and no writes. Needs only httpx (repo .venv).
"""
import argparse
import math
import sys

import httpx

LEAD_UP = [  # week 1: the 8 beats before "Snape's glare made my scar burn"
    "The Great Hall ceiling looked like the night sky and hundreds of candles floated in the air.",
    "I was starving, and the plates filled up with food all at once.",
    "Nearly Headless Nick showed us how his head nearly came off.",
    "Ron was stuffing himself with chicken legs and roast potatoes.",
    "I looked up at the staff table and saw Hagrid raise his goblet to me.",
    "Quirrell was talking to a teacher with greasy black hair and a hooked nose.",
    "The hook-nosed teacher looked past Quirrell's turban straight into my eyes.",
    "I wondered who that teacher was and why he stared at me like that.",
]

REPLAY = [  # week 3, different words, same arc, with noise beats mixed in
    "Candles were floating everywhere above the tables in the hall.",
    "I wonder whether the Quidditch team needs a new seeker this year.",
    "Food just appeared on the golden plates in front of us.",
    "Ron grabbed a pile of sausages before anyone else could.",
    "Somebody said the ghosts sometimes join the feast.",
    "At the head table Hagrid gave me a wave with his cup.",
    "That teacher with the black hair was whispering to Professor Quirrell.",
    "He turned and stared right at me over Quirrell's shoulder.",
]

UNRELATED = [  # week 3, a different day entirely
    "Aunt Petunia made me clean the kitchen floor again.",
    "Dudley threw his new video game at the wall because it was boring.",
    "Uncle Vernon read the newspaper and complained about the neighbours.",
    "I found a spider in my sock in the cupboard.",
    "The postman brought a pile of bills and nothing for me.",
    "We drove to the zoo and I talked to a snake.",
    "Mrs Figg's house smelled of cabbage and cats.",
    "I dreamed about a flying motorbike again.",
]

SHUFFLED = [REPLAY[i] for i in (7, 3, 0, 6, 2, 5, 1, 4)]  # same beats, wrong order


def embed(texts, base_url, model):
    resp = httpx.post(f"{base_url}/v1/embeddings", json={"model": model, "input": texts}, timeout=120)
    resp.raise_for_status()
    return [row["embedding"] for row in resp.json()["data"]]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def smith_waterman(lead, recent, bias, gap):
    """Best local alignment of lead-up beats (in order) inside recent beats.

    Match score = cosine - bias, so unrelated pairs score below zero.
    Normalised by len(lead): 1.0 would be every lead-up beat matched at
    cosine = bias + 1.
    """
    rows, cols = len(lead), len(recent)
    h = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    best = 0.0
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            s = cosine(lead[i - 1], recent[j - 1]) - bias
            h[i][j] = max(0.0, h[i - 1][j - 1] + s, h[i - 1][j] - gap, h[i][j - 1] - gap)
            best = max(best, h[i][j])
    return best / rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://192.168.1.23:11434")
    ap.add_argument("--model", default="nomic-embed-text")
    args = ap.parse_args()

    lead = embed(LEAD_UP, args.base_url, args.model)
    windows = {name: embed(texts, args.base_url, args.model)
               for name, texts in (("replay", REPLAY), ("shuffled", SHUFFLED), ("unrelated", UNRELATED))}

    pair = sorted(cosine(a, b) for a in lead for b in windows["unrelated"])
    print(f"unrelated pair cosine: min={pair[0]:.3f} median={pair[len(pair)//2]:.3f} max={pair[-1]:.3f}")
    matched = [cosine(lead[i], windows["replay"][j]) for i, j in ((0, 0), (1, 2), (3, 3), (4, 5), (5, 6), (6, 7))]
    print(f"paraphrase pair cosine: min={min(matched):.3f} mean={sum(matched)/len(matched):.3f}")

    ok = True
    for bias in (0.55, 0.6, 0.65):
        for gap in (0.05, 0.1):
            scores = {k: smith_waterman(lead, v, bias, gap) for k, v in windows.items()}
            sep = scores["replay"] > 2 * max(scores["unrelated"], 0.01) and scores["replay"] > scores["shuffled"]
            print(f"bias={bias} gap={gap}  replay={scores['replay']:.3f}  shuffled={scores['shuffled']:.3f}  "
                  f"unrelated={scores['unrelated']:.3f}  {'SEPARATES' if sep else 'no'}")
            if (bias, gap) == (0.6, 0.1):
                ok = sep
    print("default (bias=0.6, gap=0.1):", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
