"""Filler-scene scheduler — decides *when* to inject ambient scenes and which one.

The authored spine is the plot; ambient scenes are the elastic filler that
pads the gaps between spine scenes. This module owns the "when" and the
"which" — pure scheduling, no LLM calls, no file I/O, fully deterministic
under an injected ``random.Random``.

``pack.ambient_every`` and ``pack.ambient_pool`` are the pack-level defaults
(loaded from ``campaign.yaml``'s ``ambient`` block); an explicit constructor
argument overrides them, which is how the test-suite and one-off callers
pin the behaviour.
"""

import logging
import random
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # pragma: no cover
    from .pack import CampaignPack

log = logging.getLogger(__name__)

_UNSET = object()


def _inject_interval(every: object) -> int | None:
    """Return a usable injection interval, or None to disable injection.

    ``every`` is valid only when it is a real ``bool``-excluded ``int`` of
    at least 1 (``0``, negatives, non-ints and ``None`` all disable — the
    runtime must never see a ``TypeError`` from a bad pack value).
    """
    if (
        isinstance(every, int)
        and not isinstance(every, bool)
        and every >= 1
    ):
        return every
    return None


class AmbientScheduler:
    """Picks ambient filler scenes at regular intervals between spine scenes.

    Attributes:
        pool: the scene ids this scheduler may hand out (a *copy* — callers
            cannot mutate it through the scheduler).
        last: the id most recently returned by :meth:`pick`, or ``None``
            before the first call.
        every: the injection interval in spine scenes (``1`` = after every
            scene, ``2`` = every other, ...). Omitted (use the pack's
            ``ambient_every``). An explicit ``0``, negative, non-int or
            ``None`` disables injection entirely.
    """

    def __init__(
        self,
        pack: "CampaignPack",
        pool: Sequence[str] | None = None,
        every: object = _UNSET,
        rng: random.Random | None = None,
    ):
        self.pack = pack
        if pool is not None:
            self.pool = list(pool)
        else:
            self.pool = list(pack.ambient_scene_ids())
        if every is _UNSET:
            # Omitted: fall back to the pack's own ambient config.
            self.every = pack.ambient_every
        else:
            # Explicit (including None): taken as given; invalid values
            # disable injection rather than silently falling back.
            self.every = every
        self.rng = rng if rng is not None else random.Random()
        self.last: str | None = None

    def should_inject(self, played_spine_scenes: int) -> bool:
        """True when a filler scene should come *after* ``played_spine_scenes``
        spine scenes have aired.

        ``played_spine_scenes = 0`` is never an injection point (we don't open
        the stream on filler), and an invalid ``every`` disables the feature.
        Returns a real ``bool`` — the runtime branches on it and serialises
        scheduler state to JSON.
        """
        if not isinstance(played_spine_scenes, int) or isinstance(played_spine_scenes, bool):
            return False
        if played_spine_scenes <= 0:
            return False
        if not self.pool:
            return False
        interval = _inject_interval(self.every)
        if interval is None:
            return False
        return played_spine_scenes % interval == 0

    def pick(self) -> str | None:
        """Choose one ambient scene id from :attr:`pool`, or ``None`` when the
        pool is empty.

        Never repeats ``last`` while another alternative exists, so a short
        burst of filler doesn't play the same premise twice in a row; a
        single-entry pool repeats because it has no alternative. Deterministic
        under a seeded ``rng``.
        """
        if not self.pool:
            self.last = None
            return None
        if self.last in self.pool and len(self.pool) > 1:
            candidates = [sid for sid in self.pool if sid != self.last]
        else:
            candidates = list(self.pool)
        choice = self.rng.choice(candidates)
        self.last = choice
        log.debug("ambient pick: %s (pool=%d)", choice, len(self.pool))
        return choice
