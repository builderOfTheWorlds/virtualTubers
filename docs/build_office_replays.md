# scripts/build_office_replays.py

## Overview

OB-33. This script turns a sessionCorpus JSONL export into ashiorid_office replay episodes and
uploads them to the Rerun Theater library as **drafts**.

It sends each session through OB-12 role attribution (`app/office/role_attribution.py`
`attribute`). That step maps every event to an office seat (`speaker: tuber_N`) and re-runs the
leak audit as a hard gate.

- **Uploads are always drafts.** The script has no approve step and no flag that adds one.
- The control panel's "Drafts awaiting review" list stays the only way an office replay can go
  on air.
- Only approved replays can reach the off-hours playlist ([office_playlist.md](office_playlist.md)).

## Signature

```text
build_office_replays.py EXPORT [--out DIR] [--dry-run] [--message-api URL]
                        [--min-events N] [--tag TAG] [--session ID ...] [--limit N]
                        [--embellish] [--timeout S] [-v]
```

```python
def run(export, *, out=None, dry_run=False, message_api_url="http://127.0.0.1:8090",
        http_post=None, min_events=5, tag=None, sessions=None, limit=None,
        llm_client=None, embellish=False, timeout_s=60.0) -> dict
def upload_draft(name, episode, *, message_api_url=..., http_post=None, timeout_s=60.0) -> dict
def main(argv=None, http_post=None, llm_client=None) -> int
```

## Parameters

- `EXPORT`: the sessionCorpus export `.jsonl`, one `export_view` record per line.
  - A line that is not valid JSON is skipped with a warning that names the line number.
  - So is a record with no `events` list.
- `--out DIR`: write each episode to `DIR/<name>.json`. Required with `--dry-run`. Optional
  when uploading, where it keeps a local copy.
- `--dry-run`: write only. Nothing is uploaded.
- `--message-api URL`: the message-api base URL. Defaults to `$MESSAGE_API_URL`, or
  `http://127.0.0.1:8090` if that is unset.
- `--min-events N` (default 5): skip episodes with fewer events than this. They have nothing
  worth watching.
- `--tag TAG`: keep only records whose export `tags` list contains `TAG`, for example `feature`.
- `--session ID`: keep only this `session_id`. Repeat the flag to keep several.
- `--limit N`: stop after `N` episodes.
- `--embellish`: run the OB-12 LLM pass, which adds hand-off lines and Marketing reactions. It
  uses `llm_client.build_llm_client({})`.
- `http_post(url, content, headers, params, timeout) -> response` (injectable): the upload
  call. It has the same contract as `services/3layer-generator/draft_submitter.py`. The default
  uses httpx.

### Request sent per episode

```text
POST {message_api}/replays?status=draft&uploaded_by=build_office_replays&name=<episode source>
Content-Type: application/json
<episode JSON>
```

- The episode name is `office-<source_tool>-<session_id>`, from `role_attribution.episode_name`.
- message-api validates each upload (`episode_validator.validate_episode`) and stores it with
  `status=draft`.
- If a 2xx reply echoes any status other than `draft`, the script treats that upload as a
  failure.

## Return Value

`run` returns a summary dict:

- counts: `built`, `too_short`, `failed` (attribution or audit), `written`, `submitted`,
  `exists` (a 409), `upload_failed`
- `outcomes`: a list of `{status, name, http_status?, error?}`

`main` prints one line per episode plus a totals line, then returns an exit code:

| Code | Meaning |
|---|---|
| `0` | Every built episode was written or uploaded. A 409 counts as skipped. |
| `1` | At least one upload failed. |
| `2` | Bad input: the export is missing, or `--dry-run` was given without `--out`. |

A session that fails attribution or the audit is counted and skipped. It does not make the run
fail.

## Dependencies

- `app/office/role_attribution.py` (OB-12)
- httpx, imported lazily and only for the default poster
- message-api `POST /replays` (`services/message-api/api.py`)

## Usage Examples

```bash
# Preview: write episodes locally, upload nothing
.venv/bin/python scripts/build_office_replays.py ../sessionCorpus/data/export.jsonl \
    --dry-run --out office_replays

# Upload only feature sessions as drafts, then review them in the control panel
.venv/bin/python scripts/build_office_replays.py ../sessionCorpus/data/export.jsonl \
    --tag feature --message-api http://127.0.0.1:8090
```

```python
# Tests: a fake poster
summary = run("export.jsonl", http_post=fake_post)
assert all(c["params"]["status"] == "draft" for c in fake_post.calls)
```

## Error Handling

- **Attribution failure.** An `AttributionError` (a malformed record, or a leak-audit hit) is
  logged at ERROR with the session id only, never the content. The session is skipped.
- **Upload failure.** A transport failure, or a non-2xx reply other than 409, is recorded as
  `failed`, and the batch carries on.
- **Name clash.** A 409 is recorded as `exists`. Drafts are never overwritten, so an episode
  that was already approved can never be demoted back to a draft.
- **Logging.** Session content is never logged or printed. Only names, counts and HTTP statuses
  are.

## Changelog

- **v1.0.0** (2026-09-28): Initial version (OB-33). Dry-run and draft upload, with an
  injectable HTTP client. Tests: `tests/test_build_office_replays.py`.
