"""Gate: the only way a generated scene reaches a tracked pack.

Per §9.1.3, generated scenes must pass through load_pack() +
app/campaign/validator.validate_pack() before promotion. This module
composes those two into one function that operates on a STAGED pack — the
pack root plus a `proposed_scenes/` overlay — so we are validating the pack
that WOULD exist after promotion without touching the tracked source tree.

The API is intentionally small. Two public functions:

  check_stage(base_pack_dir, proposed_scenes_dir) -> GateResult
      Loads the pack, overlays proposed scenes into a temp dir, runs
      load_pack() and validate_pack(), returns a GateResult with pass/fail
      and the full error list.

  promote(base_pack_dir, proposed_scenes_dir, run_id) -> list[str]
      Only after a GateResult.ok is False, copies each staged scene into
      `base_pack_dir/scenes/`. Returns the list of scenes promoted. Refuses
      to run if the gate is not green — this is the load-bearing correction
      of the plan and there is no way around it in this module.

This module imports from `campaign.pack` and `campaign.validator` — both
live under `app/campaign/` in this repo. Callers must have
`app/` on their `sys.path` (root tests/conftest.py already does this).
"""
import logging
import pathlib
import shutil
import tempfile

from campaign.pack import PackError, load_pack
from campaign.validator import CampaignInvalid, validate_pack

log = logging.getLogger(__name__)


class GateError(ValueError):
    """Raised when the gate fails validation or refuses a promotion."""


class GateResult:
    """The result of a stage check: pass/fail + evidence."""
    def __init__(self, ok: bool, errors: list[str], warnings: list[str]):
        self.ok = ok
        self.errors = errors
        self.warnings = warnings

    def __bool__(self):
        return self.ok

    def summary(self) -> str:
        if self.ok:
            return f"PASS {len(self.warnings)} warning(s)"
        return f"FAIL {len(self.errors)} error(s), {len(self.warnings)} warning(s)"


def _overlay(base_dir: pathlib.Path, proposed_dir: pathlib.Path,
             tmp_root: pathlib.Path) -> pathlib.Path:
    """Copy `base_dir` into `tmp_root` and then copy every `*.yaml` from
    `proposed_dir` into `tmp_root/scenes/`. Leaves the base tree untouched —
    this is a read-only check, the base pack on disk is not modified."""
    import shutil
    tmp = tmp_root / "staged"
    shutil.copytree(base_dir, tmp, ignore=shutil.ignore_patterns("generated",
                                                                "__pycache__"))
    scenes_in = proposed_dir
    if not scenes_in.is_dir():
        raise GateError(f"proposed_scenes dir {scenes_in} does not exist")
    scenes_out = tmp / "scenes"
    scenes_out.mkdir(exist_ok=True)
    for f in sorted(scenes_in.glob("*.yaml")) + sorted(scenes_in.glob("*.yml")):
        shutil.copy2(f, scenes_out / f.name)
    return tmp


def check_stage(base_pack_dir: str | pathlib.Path,
                proposed_scenes_dir: str | pathlib.Path) -> GateResult:
    """Validate the pack as it would look after promotion. Non-destructive."""
    base = pathlib.Path(base_pack_dir).resolve()
    proposed = pathlib.Path(proposed_scenes_dir).resolve()
    if not (base / "campaign.yaml").exists():
        raise GateError(f"base pack {base} has no campaign.yaml")

    with tempfile.TemporaryDirectory(prefix="pack_gate_") as tdir:
        tmp_root = pathlib.Path(tdir)
        staged = _overlay(base, proposed, tmp_root)

        try:
            pack = load_pack(staged)
        except PackError as exc:
            log.error("load_pack failed on staged pack: %s", exc)
            return GateResult(False, [f"load_pack: {exc}"], [])

        report = validate_pack(pack)
        log.info("gate result: %d error(s), %d warning(s)",
                 len(report.errors), len(report.warnings))
        return GateResult(report.ok, list(report.errors), list(report.warnings))


def promote(base_pack_dir: str | pathlib.Path,
            proposed_scenes_dir: str | pathlib.Path,
            *, run_id: str = "") -> list[str]:
    """Copy staged scenes into the base pack's scenes/ directory.
    First re-runs the gate; refuses to promote if it fails. Returns the
    scene ids actually copied (in sort order)."""
    base = pathlib.Path(base_pack_dir).resolve()
    proposed = pathlib.Path(proposed_scenes_dir).resolve()

    result = check_stage(base, proposed)
    if not result.ok:
        log.error("promote refused: gate failed with %d error(s)", len(result.errors))
        raise GateError("promotion refused: gate failed.\n" +
                        "\n".join(f"  - {e}" for e in result.errors))

    scenes_out = base / "scenes"
    scenes_out.mkdir(exist_ok=True)
    promoted = []
    for f in sorted(proposed.glob("*.yaml")) + sorted(proposed.glob("*.yml")):
        dst = scenes_out / f.name
        shutil.copy2(f, dst)
        promoted.append(f.stem)
        log.info("promoted %s -> %s (run_id=%s)", f.name, dst, run_id or "<none>")
    return promoted
