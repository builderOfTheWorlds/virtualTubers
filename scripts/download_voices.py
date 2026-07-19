#!/usr/bin/env python3
"""
download_voices.py
Fetches the Piper .onnx voice models the replay narration uses
(app/tts_client.py) from the rhasspy/piper-voices HuggingFace repo.

Run locally, then sync the output directory to the deployment host at
/opt/virtualTubers/voices — docker-compose mounts it read-only into the
workers at /data/voices (the paths config/worker.yaml's `voice` section
points at).

    .venv/Scripts/python.exe scripts/download_voices.py --out voices

Each voice needs the .onnx model AND its .onnx.json config sidecar; both are
downloaded. Add more voices to VOICES as personas grow — browse the catalog
at https://huggingface.co/rhasspy/piper-voices
"""
import argparse
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# name -> repo path (the two default replay speakers: coder + boss).
# "low" tier is the deployed default (config/workers/*.yaml) — smaller model,
# much faster synthesis, more robotic-sounding. "medium"/"high" are kept
# here too so you can switch back (or A/B compare) without editing this file.
VOICES = {
    "en_US-lessac-low": "en/en_US/lessac/low/en_US-lessac-low.onnx",
    "en_US-ryan-low": "en/en_US/ryan/low/en_US-ryan-low.onnx",
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
}


def download(url, dest):
    print(f"  {dest.name} <- {url}")
    with urllib.request.urlopen(url) as response, open(dest, "wb") as fh:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)


def main():
    parser = argparse.ArgumentParser(description="Download Piper voice models for replay narration")
    parser.add_argument("--out", default="voices", help="Output directory (default: voices/)")
    parser.add_argument("--voices", nargs="*", default=sorted(VOICES),
                        help=f"Which voices to fetch (default: all of {sorted(VOICES)})")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    failed = 0
    for name in args.voices:
        repo_path = VOICES.get(name)
        if repo_path is None:
            print(f"  SKIP  {name}: not in VOICES — add its repo path first")
            failed += 1
            continue
        for suffix in ("", ".json"):  # model + its config sidecar
            dest = out / f"{name}.onnx{suffix}"
            if dest.exists():
                print(f"  have  {dest.name}")
                continue
            try:
                download(f"{BASE_URL}/{repo_path}{suffix}", dest)
            except OSError as exc:
                print(f"  FAIL  {dest.name}: {exc}")
                dest.unlink(missing_ok=True)
                failed += 1

    print(f"[download_voices] done -> {out} "
          f"(sync to /opt/virtualTubers/voices on the deploy host)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
