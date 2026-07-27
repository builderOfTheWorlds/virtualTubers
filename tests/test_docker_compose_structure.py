"""
Structural tests for docker-compose.yml + docker-compose.coder.yml.

Why this exists: the campaign platform build (docs/campaign_platform_build.md,
docs/blank_workers.md) replaced six hardcoded named workers
(worker-coder/-coder-native/-coder-opencode/-coder-aider/-manager/-tester)
with eight generic, interchangeable workers (worker-1..worker-8) that get
their identity assigned at runtime instead of baked into compose. The whole
point of "generic" is that the eight service blocks must never drift from
each other except in the handful of fields that are deliberately allowed to
differ (WORKER_ID, DISPLAY_NUM, and the *names* of three indexed override
env vars) — these tests catch an accidental copy-paste divergence the same
way test_build_layout.py catches a resolve_pane regression: by parsing the
real file with the real YAML loader (PyYAML resolves docker-compose.yml's
anchors/merge keys before this test ever sees a dict) and asserting on the
resulting structure, not by re-reading the YAML source as text.

They also encode *why* docker-compose.coder.yml exists as a separate overlay
file rather than a `profiles: ["coder-campaign"]` entry on the same
worker-N services (see docs/blank_workers.md's "Why an overlay file, not a
profiles: entry" section) — a regression here would mean someone "simplified"
the overlay back into something that silently breaks the always-on generic
worker guarantee.
"""
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"
CODER_OVERLAY_PATH = ROOT / "docker-compose.coder.yml"

GENERIC_WORKER_NAMES = [f"worker-{n}" for n in range(1, 9)]

# Env vars that are *expected* to differ across the eight generic workers —
# everything else in a worker's `environment` map must be byte-identical.
PER_WORKER_ENV_KEYS = {"WORKER_ID", "DISPLAY_NUM", "STREAM_KEY", "LAYOUT_PRESET", "AVATAR_PROVIDER"}

# Images this repo builds itself (install.ps1/install.sh) — only these are
# expected to carry pull_policy: never. Third-party images pulled straight
# from a registry (redis, nginx-rtmp, busybox, kafka, postgres) never had it.
CUSTOM_IMAGE_PREFIXES = ("vtube-worker:", "virtualtubers-")

# The coder-campaign role convention docs/blank_workers.md documents:
# worker-1=coder(legacy, no backend), worker-2=coder-native,
# worker-3=coder-opencode, worker-4=coder-aider, worker-5=manager,
# worker-6=tester, worker-7/8=spare.
CODER_CAMPAIGN_OVERLAY_SERVICES = {"worker-1", "worker-2", "worker-3", "worker-4", "worker-6"}
LOCAL_INFRA_SERVICES = {"kafka-init-perms", "kafka", "postgres"}


@pytest.fixture(scope="module")
def compose():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def coder_overlay():
    with open(CODER_OVERLAY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _merge_environment(base_env, overlay_env):
    """Reproduce Compose's documented merge rule for a mapping-type
    attribute: https://docs.docker.com/compose/multiple-compose-files/merge/
    — merged key-by-key, overlay wins on a shared key."""
    merged = dict(base_env or {})
    merged.update(overlay_env or {})
    return merged


def _merge_volumes(base_volumes, overlay_volumes):
    """Reproduce Compose's documented merge rule for a list-type attribute
    (volumes/ports/expose/...): concatenated."""
    return list(base_volumes or []) + list(overlay_volumes or [])


def _merge_service(base_service, overlay_service):
    """Apply the two merge rules above across one service's fragments —
    exactly what `docker compose -f docker-compose.yml -f
    docker-compose.coder.yml` does for a service name present in both files.
    """
    merged = dict(base_service)
    if "environment" in overlay_service:
        merged["environment"] = _merge_environment(base_service.get("environment"), overlay_service["environment"])
    if "volumes" in overlay_service:
        merged["volumes"] = _merge_volumes(base_service.get("volumes"), overlay_service["volumes"])
    return merged


# ── Every service is prebuilt, never `build:`-ed by compose itself ──────────

def test_no_service_anywhere_uses_a_build_block(compose):
    """install.ps1/install.sh build every image explicitly; docker-compose.yml
    must never grow a `build:` block, or a `docker compose up` on a host that
    hasn't run install.ps1/.sh yet would silently try (and fail without a
    Dockerfile context, or worse, succeed against the wrong context) instead
    of using the prebuilt image the install scripts produced."""
    for name, service in compose["services"].items():
        assert "image" in service, f"{name} has no image:"
        assert "build" not in service, f"{name} must not use a build: block"


def test_every_custom_built_image_sets_pull_policy_never(compose):
    """pull_policy: never is only meaningful for images THIS repo builds
    itself — third-party images pulled straight from a registry (redis,
    nginx-rtmp, busybox, kafka, postgres) never had it and don't need it;
    asserting it on those would be testing something that was never true
    rather than guarding a real invariant."""
    custom_services = [
        (name, service)
        for name, service in compose["services"].items()
        if service["image"].startswith(CUSTOM_IMAGE_PREFIXES)
    ]
    # sanity: this should catch all 8 workers + the 4 custom services
    assert len(custom_services) == 12
    for name, service in custom_services:
        assert service.get("pull_policy") == "never", f"{name} must set pull_policy: never"


def test_coder_overlay_never_redefines_image_or_pull_policy_or_build(coder_overlay):
    """The overlay's whole job is to ADD environment/volumes fragments — it
    must never re-declare (or worse, silently change) the image a service
    runs, and must never introduce a build step of its own."""
    for name, fragment in coder_overlay["services"].items():
        assert "image" not in fragment, f"{name} overlay fragment must not set image:"
        assert "pull_policy" not in fragment, f"{name} overlay fragment must not set pull_policy:"
        assert "build" not in fragment, f"{name} overlay fragment must not set build:"


# ── The eight generic workers are structurally identical ────────────────────

def test_all_eight_generic_workers_exist(compose):
    for name in GENERIC_WORKER_NAMES:
        assert name in compose["services"], f"missing generic worker service {name}"


def test_worker_id_and_display_num_are_correct_and_distinct_per_worker(compose):
    seen_display_nums = set()
    for n, name in enumerate(GENERIC_WORKER_NAMES, start=1):
        env = compose["services"][name]["environment"]
        assert env["WORKER_ID"] == f"worker-{n}"
        display_num = env["DISPLAY_NUM"]
        assert display_num not in seen_display_nums, "DISPLAY_NUM must be unique per worker (Xvfb isolation)"
        seen_display_nums.add(display_num)
    # extends the pre-existing 99+ numbering scheme (coder=99 .. coder-aider=104)
    assert seen_display_nums == set(range(99, 107))


def test_indexed_override_env_vars_embed_the_correct_worker_index(compose):
    for n, name in enumerate(GENERIC_WORKER_NAMES, start=1):
        env = compose["services"][name]["environment"]
        assert env["STREAM_KEY"] == f"${{WORKER_{n}_STREAM_KEY:-worker-{n}}}"
        assert env["LAYOUT_PRESET"] == f"${{WORKER_{n}_LAYOUT_PRESET:-replay}}"
        assert env["AVATAR_PROVIDER"] == f"${{WORKER_{n}_AVATAR_PROVIDER:-}}"


def test_every_other_environment_key_is_byte_identical_across_all_eight_workers(compose):
    normalized = []
    reference_keys = set(compose["services"][GENERIC_WORKER_NAMES[0]]["environment"].keys())
    for name in GENERIC_WORKER_NAMES:
        env = compose["services"][name]["environment"]
        assert set(env.keys()) == reference_keys, (
            f"{name} has a different set of environment keys than worker-1 — "
            "generic workers must only differ in value, not in which vars they define"
        )
        normalized.append({k: v for k, v in env.items() if k not in PER_WORKER_ENV_KEYS})
    first = normalized[0]
    for name, env in zip(GENERIC_WORKER_NAMES[1:], normalized[1:]):
        assert env == first, f"{name}'s non-indexed environment diverges from worker-1"


def test_volumes_are_identical_across_all_eight_workers_and_mount_the_whole_config_dir(compose):
    first = compose["services"][GENERIC_WORKER_NAMES[0]]["volumes"]
    assert "./config:/config:ro" in first, (
        "generic workers must mount the whole config directory (CONTRACT §8 — "
        "a worker resolves its persona from config/campaigns/<campaign>/personas.yaml "
        "at runtime), not a single per-worker file"
    )
    for name in GENERIC_WORKER_NAMES[1:]:
        assert compose["services"][name]["volumes"] == first, f"{name} volumes diverge from worker-1"


def test_no_generic_worker_mounts_an_old_per_worker_config_file(compose):
    # The old bind mount (`./config/workers/<x>.yaml:/config/worker.yaml:ro`)
    # must be fully gone — every generic worker reads the shared
    # config/worker.yaml base template via the whole-directory mount instead.
    for name in GENERIC_WORKER_NAMES:
        for vol in compose["services"][name]["volumes"]:
            assert "config/workers/" not in vol, f"{name} still binds an old per-worker config file: {vol}"


def test_non_volume_non_environment_fields_identical_across_all_eight_workers(compose):
    fields = ("image", "pull_policy", "ipc", "shm_size", "restart", "depends_on")
    first = {f: compose["services"][GENERIC_WORKER_NAMES[0]].get(f) for f in fields}
    for name in GENERIC_WORKER_NAMES[1:]:
        actual = {f: compose["services"][name].get(f) for f in fields}
        assert actual == first, f"{name} diverges from worker-1 in {fields}"


def test_generic_workers_carry_no_profiles_key_because_blank_means_disabled_not_absent(compose):
    # A worker with no persona assigned must still be a running container
    # (app/worker_control.py's fail-open is_enabled), so no generic worker
    # may be profile-gated.
    for name in GENERIC_WORKER_NAMES:
        assert "profiles" not in compose["services"][name], (
            f"{name} must always start — profiles: would make it conditional"
        )


# ── local-infra profile is preserved verbatim ────────────────────────────────

def test_local_infra_services_carry_exactly_that_profile(compose):
    """local-infra is the pre-existing profile pattern this build explicitly
    preserves verbatim (recon_platform.md §6) — this just guards against an
    accidental edit while touching the rest of the file."""
    for name in LOCAL_INFRA_SERVICES:
        assert compose["services"][name]["profiles"] == ["local-infra"]


def test_no_service_depends_on_a_local_infra_service(compose):
    # "No depends_on linking the workers/services to these on purpose —
    # adding one would force this profile on every deployment" (verbatim
    # inline comment in docker-compose.yml).
    for name, service in compose["services"].items():
        if name in LOCAL_INFRA_SERVICES:
            continue
        depends_on = service.get("depends_on") or []
        if isinstance(depends_on, dict):
            depends_on = list(depends_on.keys())
        assert not (set(depends_on) & LOCAL_INFRA_SERVICES), f"{name} must not depend_on a local-infra service"


# ── docker-compose.coder.yml overlay ─────────────────────────────────────────

def test_overlay_only_touches_the_documented_generic_worker_services(coder_overlay):
    """docker-compose.coder.yml adds the A/B coding-backend workspace wiring
    (docs/blank_workers.md) on top of specific generic workers, by
    CONVENTION (worker-1=coder, worker-2=coder-native,
    worker-3=coder-opencode, worker-4=coder-aider, worker-6=tester) —
    matching app/agent.py's WORKSPACE_MOUNT_PATTERN =
    "/data/repos/{coder_id}" where coder_id is the sending container's own
    WORKER_ID."""
    assert set(coder_overlay["services"].keys()) == CODER_CAMPAIGN_OVERLAY_SERVICES


def test_overlay_services_only_set_environment_or_volumes(coder_overlay):
    for name, fragment in coder_overlay["services"].items():
        assert set(fragment.keys()) <= {"environment", "volumes"}, (
            f"{name} overlay fragment sets an unexpected top-level key — "
            "the overlay's whole job is ADDING env/volumes to an existing service"
        )


def test_overlay_carries_no_profiles_key_anywhere(coder_overlay):
    # See docs/blank_workers.md: a `profiles:` entry here would, via
    # Compose's list-concatenation merge rule, silently gate the merged
    # worker-N service off by default the moment this file is loaded — the
    # file's own inclusion (via -f/COMPOSE_FILE) is the only opt-in switch.
    for name, fragment in coder_overlay["services"].items():
        assert "profiles" not in fragment, f"{name} overlay fragment must not carry profiles:"


def test_overlay_service_names_are_a_subset_of_base_generic_workers(compose, coder_overlay):
    assert set(coder_overlay["services"].keys()) <= set(GENERIC_WORKER_NAMES)
    for name in coder_overlay["services"]:
        assert name in compose["services"]


@pytest.mark.parametrize(
    "worker_name,expected_backend,expected_volume_target",
    [
        ("worker-2", "native", "repo-native:/data/repo"),
        ("worker-3", "opencode", "repo-opencode:/data/repo"),
        ("worker-4", "aider", "repo-aider:/data/repo"),
    ],
)
def test_coding_backend_workers_get_their_own_workspace_and_env_override_when_merged(
    compose, coder_overlay, worker_name, expected_backend, expected_volume_target
):
    merged = _merge_service(compose["services"][worker_name], coder_overlay["services"][worker_name])
    assert merged["environment"]["CODING_BACKEND"] == expected_backend
    assert merged["environment"]["WORKSPACE_PATH"] == "/data/repo"
    assert expected_volume_target in merged["volumes"]
    # base fields (e.g. WORKER_ID) must survive the merge untouched
    assert merged["environment"]["WORKER_ID"] == worker_name


def test_legacy_coder_worker_gets_repo_volume_but_no_coding_backend_env_when_merged(compose, coder_overlay):
    merged = _merge_service(compose["services"]["worker-1"], coder_overlay["services"]["worker-1"])
    assert "repo:/data/repo" in merged["volumes"]
    assert "CODING_BACKEND" not in merged["environment"]
    assert "WORKSPACE_PATH" not in merged["environment"]


def test_tester_worker_sees_every_coders_workspace_readonly_at_the_conventional_path_when_merged(
    compose, coder_overlay
):
    # This is the exact property CONTRACT §2 in the campaign-platform build
    # asked to be preserved: "if a coder campaign is running, the tester
    # must still see every coder workspace" — reproducing app/agent.py's
    # WORKSPACE_MOUNT_PATTERN = "/data/repos/{coder_id}" with coder_id ==
    # each coding-backend worker's own WORKER_ID.
    merged = _merge_service(compose["services"]["worker-6"], coder_overlay["services"]["worker-6"])
    expected_mounts = {
        "repo:/data/repos/worker-1:ro",
        "repo-native:/data/repos/worker-2:ro",
        "repo-opencode:/data/repos/worker-3:ro",
        "repo-aider:/data/repos/worker-4:ro",
    }
    assert expected_mounts <= set(merged["volumes"])


def test_untouched_generic_workers_are_unaffected_by_the_coder_overlay(compose, coder_overlay):
    for name in ("worker-5", "worker-7", "worker-8"):
        assert name not in coder_overlay["services"]
        # sanity: still exactly the base definition
        assert compose["services"][name]["environment"]["WORKER_ID"] == name


# ── top-level volumes declare everything referenced ──────────────────────────

def test_coder_campaign_named_volumes_still_declared_at_top_level(compose):
    # docker-compose.coder.yml references these by name but does not declare
    # them itself — Compose requires the merged config's top-level
    # `volumes:` section to declare every named volume any service
    # references, so they must live in docker-compose.yml.
    for vol in ("repo", "repo-native", "repo-opencode", "repo-aider"):
        assert vol in compose["volumes"]


def test_core_named_volumes_still_declared(compose):
    for vol in ("world-state", "redis-data", "kafka-data", "postgres-data"):
        assert vol in compose["volumes"]
