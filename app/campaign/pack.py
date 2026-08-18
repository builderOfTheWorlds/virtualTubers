"""Load a campaign pack from disk into typed objects."""

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any

import yaml

log = logging.getLogger(__name__)


class PackError(ValueError):
    """Raised for every structural problem in a campaign pack."""


def _load_yaml(path):
    """Load a YAML file, raising PackError on any failure."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as exc:
        raise PackError(f"failed to load {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PackError(f"failed to load {path}: {exc}") from exc
    return data


def _load_cast_member(cast_dir, member_id, role):
    """Load one cast member's YAML file."""
    path = cast_dir / f"{member_id}.yaml"
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise PackError(f"cast member {member_id} is not a mapping")
    name = data.get("name")
    if not name:
        raise PackError(f"cast member {member_id} missing 'name'")
    return CastMember(
        id=member_id,
        name=name,
        role=role,
        archetype=data.get("archetype"),
        system_prompt=data.get("system_prompt"),
        voice=data.get("voice"),
        avatar=data.get("avatar"),
    )


def _build_beat(data, scene_id, index):
    """Build one beat from parsed YAML."""
    kind = data.get("type")
    if not kind:
        raise PackError(f"beat in {scene_id} missing 'type'")
    speaker = data.get("speaker")
    primitive = data.get("primitive")
    params = data.get("params") or {}
    improv = bool(data.get("improv"))
    show = data.get("show")

    text = data.get("text")
    if text is not None:
        if isinstance(text, list):
            if not all(isinstance(s, str) for s in text):
                raise PackError(f"beat in {scene_id} has non-string entry in text list")
            texts = text
            text = text[0] if text else None
        elif isinstance(text, str):
            texts = [text]
        else:
            raise PackError(f"beat in {scene_id} has invalid text type")
    else:
        texts = []

    return Beat(
        kind=kind,
        speaker=speaker,
        text=text,
        primitive=primitive,
        params=params,
        improv=improv,
        show=show,
        texts=texts,
        key=f"{scene_id}#{index}",
    )


def _build_branch(data):
    """Build one branch from parsed YAML."""
    bid = data.get("id")
    if not bid:
        raise PackError("branch missing 'id'")
    next_ = data.get("next")
    if not next_:
        raise PackError("branch missing 'next'")
    when = data.get("when") or {}
    return Branch(id=bid, next=next_, when=when)


def _build_scene(data, scene_id):
    """Build one scene from parsed YAML."""
    title = data.get("title")
    enter_narration = data.get("enter_narration")
    beats_data = data.get("beats") or []
    branches_data = data.get("branches") or []
    default_next = data.get("default_next")

    if not isinstance(beats_data, list):
        raise PackError(f"scene {scene_id} has non-list 'beats'")
    if not isinstance(branches_data, list):
        raise PackError(f"scene {scene_id} has non-list 'branches'")

    beats = [_build_beat(beat_data, scene_id, i)
             for i, beat_data in enumerate(beats_data)]

    branches = [_build_branch(branch_data)
                for branch_data in branches_data]

    ambient = data.get("ambient")
    if ambient is not None and not isinstance(ambient, bool):
        raise PackError(f"scene {scene_id} has non-bool 'ambient'")
    ambient = bool(ambient)

    prompt = data.get("prompt")
    lore = data.get("lore") or []

    return Scene(
        id=scene_id,
        title=title,
        enter_narration=enter_narration,
        beats=beats,
        branches=branches,
        default_next=default_next,
        ambient=ambient,
        prompt=prompt,
        lore=lore,
    )


def _load_ambient_config(campaign_data):
    """Load the ambient configuration from campaign.yaml."""
    ambient_config = campaign_data.get("ambient")
    if ambient_config is None:
        return 0, []
    if not isinstance(ambient_config, dict):
        raise PackError("campaign.yaml has non-mapping 'ambient'")
    every = ambient_config.get("every")
    if every is not None:
        if not isinstance(every, int) or isinstance(every, bool):
            raise PackError("campaign.yaml 'ambient.every' must be a non-negative integer")
        if every < 0:
            raise PackError("campaign.yaml 'ambient.every' must be a non-negative integer")
    else:
        every = 0
    pool = ambient_config.get("pool") or []
    return every, list(pool)


def load_pack(path):
    """Load a campaign pack from disk into typed objects."""
    root = pathlib.Path(path).resolve()
    log.debug("loading campaign pack %s", root)

    campaign_path = root / "campaign.yaml"
    if not campaign_path.exists():
        raise PackError(f"campaign.yaml not found in {root}")
    campaign_data = _load_yaml(campaign_path)
    if not isinstance(campaign_data, dict):
        raise PackError("campaign.yaml is not a mapping")

    name = campaign_data.get("name")
    if not name:
        raise PackError("campaign.yaml missing 'name'")

    title = campaign_data.get("title")
    genre = campaign_data.get("genre")
    start_scene = campaign_data.get("start_scene")
    if not start_scene:
        raise PackError("campaign.yaml missing 'start_scene'")
    gm_id = campaign_data.get("gm")
    if not gm_id:
        raise PackError("campaign.yaml missing 'gm'")

    players = campaign_data.get("players") or []
    primitives = campaign_data.get("primitives") or []
    theme = campaign_data.get("theme") or {}

    cast_dir = root / "cast"
    if not cast_dir.exists():
        raise PackError(f"cast directory not found in {root}")
    scenes_dir = root / "scenes"
    if not scenes_dir.exists():
        raise PackError(f"scenes directory not found in {root}")

    # Load cast
    cast = {}
    member_ids = [gm_id] + players
    for member_id in member_ids:
        member = _load_cast_member(cast_dir, member_id, "gm" if member_id == gm_id else "player")
        cast[member_id] = member

    # Load scenes
    scenes = {}
    scene_files = sorted(scenes_dir.glob("*.yaml")) + sorted(scenes_dir.glob("*.yml"))
    for scene_file in scene_files:
        data = _load_yaml(scene_file)
        if not isinstance(data, dict):
            raise PackError(f"scene file {scene_file.name} is not a mapping")
        scene_id = data.get("id")
        if not scene_id:
            raise PackError(f"scene file {scene_file.name} missing 'id'")
        if scene_id in scenes:
            raise PackError(f"duplicate scene id {scene_id!r}")
        scenes[scene_id] = _build_scene(data, scene_id)

    # Verify start scene exists
    if start_scene not in scenes:
        raise PackError(f"start scene {start_scene!r} not found among scenes")

    # Load lore
    lore_dir = root / "lore"
    lore = {}
    if lore_dir.is_dir():
        for lore_file in lore_dir.glob("*.md"):
            try:
                with open(lore_file, encoding="utf-8") as f:
                    lore[lore_file.stem] = f.read()
            except OSError as exc:
                raise PackError(f"failed to read lore file {lore_file}: {exc}") from exc
    else:
        lore_dir = None

    # Load ambient config
    ambient_every, ambient_pool = _load_ambient_config(campaign_data)

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
        lore=lore,
        ambient_every=ambient_every,
        ambient_pool=ambient_pool,
    )

    log.info("loaded campaign pack %s with %d scenes and %d cast members",
             name, len(scenes), len(cast))

    return pack


@dataclass
class Beat:
    kind: str
    speaker: str | None = None
    text: str | None = None
    primitive: str | None = None
    params: dict = field(default_factory=dict)
    improv: bool = False
    show: str | None = None
    texts: list[str] = field(default_factory=list)
    key: str = ""


@dataclass
class Branch:
    id: str
    next: str
    when: dict = field(default_factory=dict)


@dataclass
class Scene:
    id: str
    title: str | None = None
    enter_narration: str | None = None
    beats: list[Beat] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    default_next: str | None = None
    ambient: bool = False
    prompt: str | None = None
    lore: list[str] = field(default_factory=list)


@dataclass
class CastMember:
    id: str
    name: str
    role: str
    archetype: str | None = None
    system_prompt: str | None = None
    voice: str | None = None
    avatar: str | None = None


@dataclass
class CampaignPack:
    name: str
    title: str | None
    genre: str | None
    start_scene: str
    gm_id: str
    player_ids: list[str]
    primitives: list[str]
    theme: dict
    cast: dict[str, CastMember]
    scenes: dict[str, Scene]
    root: pathlib.Path
    lore_dir: pathlib.Path | None
    lore: dict[str, str] = field(default_factory=dict)
    ambient_every: int = 0
    ambient_pool: list[str] = field(default_factory=list)

    def scene(self, scene_id) -> Scene:
        try:
            return self.scenes[scene_id]
        except KeyError:
            raise PackError(f"no scene {scene_id!r}") from None

    def member(self, member_id) -> CastMember:
        try:
            return self.cast[member_id]
        except KeyError:
            raise PackError(f"no cast member {member_id!r}") from None

    def ambient_scene_ids(self) -> list[str]:
        if self.ambient_pool:
            return list(self.ambient_pool)
        return sorted([sid for sid, scene in self.scenes.items() if scene.ambient])
