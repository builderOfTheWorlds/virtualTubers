"""Load a campaign pack from disk into typed objects."""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional

import yaml

log = logging.getLogger(__name__)


class PackError(ValueError):
    """Raised for every structural problem in a campaign pack."""
    pass


@dataclass
class Beat:
    kind: str
    speaker: Optional[str] = None
    text: Optional[str] = None
    primitive: Optional[str] = None
    params: dict = field(default_factory=dict)
    improv: bool = False
    show: Optional[str] = None


@dataclass
class Branch:
    id: str
    next: str
    when: dict = field(default_factory=dict)


@dataclass
class Scene:
    id: str
    title: Optional[str] = None
    enter_narration: Optional[str] = None
    beats: List[Beat] = field(default_factory=list)
    branches: List[Branch] = field(default_factory=list)
    default_next: Optional[str] = None


@dataclass
class CastMember:
    id: str
    name: str
    role: str
    archetype: Optional[str] = None
    system_prompt: Optional[str] = None
    voice: Optional[str] = None
    avatar: Optional[str] = None


@dataclass
class CampaignPack:
    name: str
    title: Optional[str]
    genre: Optional[str]
    start_scene: str
    gm_id: str
    player_ids: List[str]
    primitives: List[str]
    theme: dict
    cast: Dict[str, CastMember]
    scenes: Dict[str, Scene]
    root: Path
    lore_dir: Optional[Path]

    def scene(self, scene_id) -> Scene:
        try:
            return self.scenes[scene_id]
        except KeyError:
            raise PackError(f"no scene {scene_id!r}")

    def member(self, member_id) -> CastMember:
        try:
            return self.cast[member_id]
        except KeyError:
            raise PackError(f"no cast member {member_id!r}")


def _load_yaml(path):
    """Load a YAML file, wrapping any error in PackError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise PackError(f"failed to load {path}: {exc}") from exc


def _load_cast_member(cast_dir, member_id, role):
    """Load one cast member's YAML file."""
    path = cast_dir / f"{member_id}.yaml"
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise PackError(f"cast member {member_id!r} YAML is not a mapping")
    name = data.get("name")
    if name is None:
        raise PackError(f"cast member {member_id!r} has no 'name'")
    return CastMember(
        id=member_id,
        name=name,
        role=role,
        archetype=data.get("archetype"),
        system_prompt=data.get("system_prompt"),
        voice=data.get("voice"),
        avatar=data.get("avatar"),
    )


def load_pack(path) -> CampaignPack:
    """Load a campaign pack from disk."""
    root = Path(path).resolve()
    log.debug("loading campaign pack %s", root)

    if not root.exists():
        raise PackError(f"campaign directory {root} does not exist")

    campaign_path = root / "campaign.yaml"
    if not campaign_path.exists():
        raise PackError(f"campaign.yaml not found in {root}")

    campaign_data = _load_yaml(campaign_path)
    if not isinstance(campaign_data, dict):
        raise PackError("campaign.yaml is not a mapping")

    required_keys = ["name", "start_scene", "gm"]
    for key in required_keys:
        if key not in campaign_data:
            raise PackError(f"campaign.yaml missing required key {key!r}")

    name = campaign_data["name"]
    title = campaign_data.get("title")
    genre = campaign_data.get("genre")
    start_scene = campaign_data["start_scene"]
    gm_id = campaign_data["gm"]
    players = campaign_data.get("players") or []
    primitives = campaign_data.get("primitives") or []
    theme = campaign_data.get("theme") or {}

    cast_dir = root / "cast"
    if not cast_dir.exists():
        raise PackError(f"cast directory not found in {root}")

    # Load cast members
    cast = {}
    cast[gm_id] = _load_cast_member(cast_dir, gm_id, "gm")
    for player_id in players:
        cast[player_id] = _load_cast_member(cast_dir, player_id, "player")

    scenes_dir = root / "scenes"
    if not scenes_dir.exists():
        raise PackError(f"scenes directory not found in {root}")

    scenes = {}
    scene_files = sorted(scenes_dir.glob("*.yaml")) + sorted(scenes_dir.glob("*.yml"))
    for scene_file in scene_files:
        log.debug("loading scene %s", scene_file)
        scene_data = _load_yaml(scene_file)
        if not isinstance(scene_data, dict):
            raise PackError(f"scene file {scene_file} is not a mapping")
        scene_id = scene_data.get("id")
        if scene_id is None:
            raise PackError(f"scene file {scene_file} has no 'id'")
        if scene_id in scenes:
            raise PackError(f"duplicate scene id {scene_id!r}")
        beats_data = scene_data.get("beats") or []
        if not isinstance(beats_data, list):
            raise PackError(f"scene {scene_id!r} 'beats' is not a list")
        beats = []
        for beat_data in beats_data:
            if not isinstance(beat_data, dict):
                raise PackError(f"scene {scene_id!r} beat is not a mapping")
            kind = beat_data.get("type")
            if kind is None:
                raise PackError(f"scene {scene_id!r} beat has no 'type'")
            beats.append(Beat(
                kind=kind,
                speaker=beat_data.get("speaker"),
                text=beat_data.get("text"),
                primitive=beat_data.get("primitive"),
                params=beat_data.get("params") or {},
                improv=beat_data.get("improv", False),
                show=beat_data.get("show"),
            ))
        branches_data = scene_data.get("branches") or []
        if not isinstance(branches_data, list):
            raise PackError(f"scene {scene_id!r} 'branches' is not a list")
        branches = []
        for branch_data in branches_data:
            if not isinstance(branch_data, dict):
                raise PackError(f"scene {scene_id!r} branch is not a mapping")
            bid = branch_data.get("id")
            if bid is None:
                raise PackError(f"scene {scene_id!r} branch has no 'id'")
            next_id = branch_data.get("next")
            if next_id is None:
                raise PackError(f"scene {scene_id!r} branch has no 'next'")
            branches.append(Branch(
                id=bid,
                next=next_id,
                when=branch_data.get("when") or {},
            ))
        scenes[scene_id] = Scene(
            id=scene_id,
            title=scene_data.get("title"),
            enter_narration=scene_data.get("enter_narration"),
            beats=beats,
            branches=branches,
            default_next=scene_data.get("default_next"),
        )

    if start_scene not in scenes:
        raise PackError(f"start scene {start_scene!r} not found among scenes")

    lore_dir = root / "lore"
    if not lore_dir.exists():
        lore_dir = None

    pack = CampaignPack(
        name=name,
        title=title,
        genre=genre,
        start_scene=start_scene,
        gm_id=gm_id,
        player_ids=list(players),
        primitives=primitives,
        theme=theme,
        cast=cast,
        scenes=scenes,
        root=root,
        lore_dir=lore_dir,
    )

    log.info("loaded campaign pack %s with %d scenes and %d cast members",
             name, len(scenes), len(cast))

    return pack
