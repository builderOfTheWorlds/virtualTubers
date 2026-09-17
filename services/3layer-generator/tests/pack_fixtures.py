"""Shared YAML fixtures for pack-content tests (store round-trips, API
endpoints, FakeStore-backed dispatch tests).

MINIMAL_PACK is the SMALLEST pack the real load_pack() accepts, built against
the actual validator in app/campaign/pack.py (2026-09):
  * campaign.yaml needs name, start_scene, gm; `players` is optional
  * load_pack() requires the cast/ AND scenes/ DIRECTORIES to exist even when
    empty
  * start_scene must name a scene that exists in the scenes directory
  * a cast member's yaml needs at least a `name`
  * a scene file needs an `id`
Nothing else is checked at load time (default_next/branch targets are NOT
validated by load_pack — keep the minimal scene self-referential so packs
using it stay loadable after the start-scene-delete tests delete other scenes).
"""

CAMPAIGN_YAML_FIXTURE = (
    "name: alpha\n"
    "title: Alpha Test Pack\n"
    "start_scene: intro\n"
    "gm: gm\n"
)

CAST_YAML_FIXTURE = (
    "name: GM Alpha\n"
    "archetype: narrator\n"
    "system_prompt: You are the GM of a small test pack.\n"
)

SCENE_YAML_FIXTURE = (
    "id: intro\n"
    "title: The Intro\n"
    "enter_narration: The pack begins.\n"
    "default_next: intro\n"
    "beats:\n"
    "  - type: narration\n"
    "    speaker: gm\n"
    "    improv: true\n"
)

MINIMAL_PACK = {
    "campaign": CAMPAIGN_YAML_FIXTURE,
    "cast": {"gm": CAST_YAML_FIXTURE},
    "scenes": {"intro": SCENE_YAML_FIXTURE},
    "lore": {"the-incident": "A short lore note for tests.\n"},
}


def seed_minimal_pack(store, name: str = "alpha") -> None:
    """Write MINIMAL_PACK's rows through a store-shaped surface (the real
    generation_store or a FakeStore) under the given pack_name."""
    import yaml
    campaign = dict(yaml.safe_load(CAMPAIGN_YAML_FIXTURE))
    campaign["name"] = name
    store.upsert_campaign(name, yaml.safe_dump(campaign, sort_keys=False))
    for member_id, body in MINIMAL_PACK["cast"].items():
        store.upsert_cast_member(name, member_id, body)
    for scene_id, body in MINIMAL_PACK["scenes"].items():
        scene = dict(yaml.safe_load(body))
        scene["id"] = scene_id
        store.upsert_scene(name, scene_id, yaml.safe_dump(scene, sort_keys=False))
    for lore_name, text in MINIMAL_PACK["lore"].items():
        store.upsert_lore(name, lore_name, text)
