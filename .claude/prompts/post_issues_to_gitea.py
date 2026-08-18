"""Post the 3LayersWeeklyGeneration review backlog to Gitea as issues.

Reads .claude/prompts/three_layers_generation_issues.md, splits it on `## `
headers, and creates one issue per section. Idempotent: skips any title that
already exists as an open or closed issue in the repo.

Token is read from the GITEA_TOKEN env var, or ~/.gitea_token if unset.
Never printed, never logged.

Usage:
    GITEA_TOKEN=... .venv/bin/python .claude/prompts/post_issues_to_gitea.py [--dry-run]
"""
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "http://192.168.1.120:3300"
OWNER = "gitea_admin"
REPO = "virtualTubers"
SRC = pathlib.Path(__file__).resolve().parent / "three_layers_generation_issues.md"


def load_token() -> str:
    token = os.environ.get("GITEA_TOKEN", "").strip()
    if token:
        return token
    path = pathlib.Path.home() / ".gitea_token"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    sys.exit("ERROR: no token. Set GITEA_TOKEN or write it to ~/.gitea_token")


def api(method: str, path: str, token: str, payload=None):
    url = f"{BASE}/api/v1{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:300]
        sys.exit(f"ERROR: {method} {path} -> HTTP {exc.code}: {detail}")


def parse_sections(text: str):
    """Split the backlog markdown into (title, body) pairs."""
    sections = []
    title, buf = None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if title:
                sections.append((title, "\n".join(buf).strip()))
            # strip the leading "N. " ordinal from the heading
            heading = line[3:].strip()
            head, sep, rest = heading.partition(". ")
            title = rest if sep and head.isdigit() else heading
            buf = []
        elif title:
            buf.append(line)
    if title:
        sections.append((title, "\n".join(buf).strip()))
    return sections


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    token = load_token()

    sections = parse_sections(SRC.read_text(encoding="utf-8"))
    if not sections:
        sys.exit(f"ERROR: no `## ` sections found in {SRC}")

    existing = set()
    for state in ("open", "closed"):
        page = 1
        while True:
            batch = api("GET", f"/repos/{OWNER}/{REPO}/issues?state={state}"
                               f"&limit=50&page={page}", token) or []
            existing.update(i["title"] for i in batch)
            if len(batch) < 50:
                break
            page += 1

    created = skipped = 0
    for title, body in sections:
        if title in existing:
            print(f"skip (exists): {title}")
            skipped += 1
            continue
        if dry_run:
            print(f"would create: {title}  ({len(body)} chars)")
            created += 1
            continue
        issue = api("POST", f"/repos/{OWNER}/{REPO}/issues", token,
                    {"title": title, "body": body})
        print(f"created #{issue['number']}: {title}")
        created += 1

    verb = "would create" if dry_run else "created"
    print(f"\n{verb} {created}, skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
