#!/usr/bin/env python3
"""
campaign_control.py
Redis-backed active-campaign + per-worker persona assignment (CONTRACT.md
§8, docs/campaign_platform_build.md's "Blank workers + runtime persona
assignment"). Lives alongside worker_control.py's worker:{id}:enabled in the
SAME Redis instance, with two more key shapes:

    campaign:active            JSON {"campaign":..., "cast": {...}, "started_at":...}
    worker:{worker_id}:persona JSON {"campaign":..., "speaker":...}

Mirrors worker_control.py's shape on purpose: identical Redis client
construction (same socket_timeout, same decode_responses=True), the same
from_config classmethod, and the same fail-open-on-read / propagate-on-write
split. Reads fail open to None ("no campaign" / "no persona"). A worker with
no persona resolved treats itself exactly like a disabled worker
(app/agent.py's tick loop -- blank == disabled, no separate mode; see
docs/blank_workers.md). This is the OPPOSITE polarity from
worker_control.WorkerControl.is_enabled's own fail-open default (True) --
that asymmetry is intentional: when Redis can't be reached, "assume this
worker is still enabled" is the safe default for an already-running stream,
but "assume this worker has a persona" is not a safe guess, since we
genuinely don't know one -- an inert/blank worker is the safer fallback than
narrating in character as a persona that might not (any longer) be
assigned to it.

Writes do NOT fail open -- they propagate redis.RedisError so the caller
(services/message-api/api.py) can 503 the operator rather than silently
reporting a campaign switch that didn't actually take.

message-api NEVER reads a persona FILE (config/campaigns/<campaign>/
personas.yaml) -- it only ever stores {campaign, speaker} pairs here in
Redis via start()/get_persona(). Each worker resolves its own persona DOC
from its own mounted config at /config/campaigns/<campaign>/personas.yaml
(app/agent.py's resolve_persona/_load_persona_doc) -- this is what keeps
message-api dumb, per CONTRACT.md §8.
"""
import json
import time

import redis

from worker_control import resolve_redis_url

CAMPAIGN_KEY = "campaign:active"


def persona_key(worker_id):
    return f"worker:{worker_id}:persona"


class CampaignControl:
    def __init__(self, redis_url, socket_timeout=2):
        self._client = redis.Redis.from_url(
            redis_url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )

    @classmethod
    def from_config(cls, config=None):
        return cls(resolve_redis_url(config))

    def _get_json(self, key, *, label):
        """Shared read-path for get_active/get_persona: fails OPEN (returns
        None) on a Redis outage OR a malformed stored value -- a control-
        plane hiccup or a corrupt key must never crash a caller that's just
        asking "what's assigned right now"."""
        try:
            value = self._client.get(key)
        except redis.RedisError as exc:
            print(f"[campaign_control] WARN redis unreachable, failing open (no {label}): {exc}")
            return None
        if not value:
            return None
        try:
            return json.loads(value)
        except ValueError:
            print(f"[campaign_control] WARN malformed {key!r} value, treating as no {label}")
            return None

    def get_active(self):
        """The currently active campaign, or None (fails open) when nothing
        is active, Redis is unreachable, or the stored value is corrupt."""
        return self._get_json(CAMPAIGN_KEY, label="active campaign")

    def get_persona(self, worker_id):
        """This worker's current {"campaign":..., "speaker":...} assignment,
        or None (fails open) when unassigned, Redis is unreachable, or the
        stored value is corrupt. Callers resolve the actual persona DOC
        themselves from their own mounted config (agent.py's
        resolve_persona) -- this method never reads a persona file."""
        return self._get_json(persona_key(worker_id), label=f"persona for {worker_id}")

    def start(self, campaign, cast, *, force=False):
        """Start `campaign` with `cast` ({speaker: worker_id}): writes
        campaign:active and one worker:{id}:persona key per cast entry.

        Any worker that was cast in a PREVIOUSLY active campaign but is NOT
        named in this new cast's worker ids goes back to blank (its old
        persona key is cleared) -- otherwise a worker left out of a new cast
        would keep performing its old persona forever, since nothing else
        ever tells it to stop. This is the one piece of behavior beyond a
        literal "write both key kinds" that CONTRACT.md §8 doesn't spell
        out; it is the only reading that keeps "start" actually replace the
        active roster rather than merge into it.

        `force` is accepted here to match the frozen CONTRACT.md §8 call
        signature (and the POST /campaigns/{campaign}/start request body)
        but is NOT used by this method itself -- the guard it exists for
        (refuse to reassign a worker mid-airing unless force=True) reads
        worker:{id}:airing, which is airing state owned by
        app/replay_pane.py, not campaign-assignment state owned here. That
        check lives one layer up, in services/message-api/api.py, BEFORE
        this method is ever called.

        Propagates redis.RedisError (does not fail open) -- like
        WorkerControl.set_enabled, so message-api can 503 the operator
        rather than report success on a write that didn't take.
        """
        previous = self.get_active()
        new_worker_ids = set(cast.values())
        for worker_id in (previous or {}).get("cast", {}).values():
            if worker_id not in new_worker_ids:
                self._client.delete(persona_key(worker_id))

        active = {"campaign": campaign, "cast": dict(cast), "started_at": time.time()}
        self._client.set(CAMPAIGN_KEY, json.dumps(active))
        for speaker, worker_id in cast.items():
            self._client.set(persona_key(worker_id), json.dumps({"campaign": campaign, "speaker": speaker}))
        return active

    def stop(self):
        """Clear campaign:active and every persona key it named -- every
        cast worker goes back to blank (== disabled).

        Reads campaign:active directly (NOT via get_active(), which fails
        open) so a Redis outage propagates here too: a stop() that silently
        no-oped on a flaky connection would leave the operator believing the
        campaign ended when the persona keys are still live. Propagates
        redis.RedisError, matching every other write in this class."""
        value = self._client.get(CAMPAIGN_KEY)
        active = None
        if value:
            try:
                active = json.loads(value)
            except ValueError:
                active = None
        for worker_id in (active or {}).get("cast", {}).values():
            self._client.delete(persona_key(worker_id))
        self._client.delete(CAMPAIGN_KEY)
