"""Run a campaign show, tracking position and driving advancement."""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from campaign.pack import CampaignPack, Scene
from campaign.scene_graph import BranchSelector, SceneGraph

log = logging.getLogger(__name__)


class CampaignRuntimeError(RuntimeError):
    """Raised for every operational fault in campaign runtime."""
    pass


@dataclass
class CampaignState:
    """The position of a running campaign."""
    campaign: str
    scene_id: str | None
    context: dict = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    loop: int = 1
    carry: dict = field(default_factory=dict)
    finished: bool = False

    def to_dict(self) -> dict:
        """Convert to a plain dictionary for JSON serialization."""
        return {
            "campaign": self.campaign,
            "scene_id": self.scene_id,
            "context": self.context,
            "history": self.history,
            "loop": self.loop,
            "carry": self.carry,
            "finished": self.finished,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CampaignState":
        """Build a state from an already-validated mapping.

        Absent optional keys fall back to the dataclass defaults. Validation
        of the payload belongs to CampaignRuntime.load(), not here.
        """
        return cls(
            campaign=data["campaign"],
            scene_id=data["scene_id"],
            context=data.get("context", {}),
            history=data.get("history", []),
            loop=data.get("loop", 1),
            carry=data.get("carry", {}),
            finished=data.get("finished", False),
        )


class CampaignRuntime:
    """The object that holds a position in a running show and drives it forward."""

    def __init__(self, pack: CampaignPack, renderer=None, graph=None, selector=None,
                 state_file=None):
        self.pack = pack
        self.renderer = renderer
        self.graph = graph or SceneGraph(pack)
        self.selector = selector
        self.state_file = state_file

        # Initial state
        self.state = CampaignState(
            campaign=pack.name,
            scene_id=pack.start_scene,
        )

    @property
    def current_scene(self) -> Scene | None:
        """Get the currently active scene."""
        if self.state.scene_id is None:
            return None
        return self.pack.scene(self.state.scene_id)

    def update_context(self, mapping: dict) -> None:
        """Merge context updates into the current state."""
        self.state.context.update(mapping)

    def play_scene(self) -> list:
        """Render the current scene and record it in history."""
        if self.state.finished:
            raise CampaignRuntimeError("cannot play scene on a finished show")

        log.debug("playing scene %s", self.state.scene_id)
        # Record before rendering so history is correct even if renderer fails
        self.state.history.append(self.state.scene_id)

        if self.renderer is not None:
            return self.renderer.render_scene(self.current_scene, context=self.state.context)
        else:
            return []

    def advance(self, selector: BranchSelector | None = None) -> str | None:
        """Move to the next scene in the graph."""
        if self.state.finished:
            raise CampaignRuntimeError("cannot advance a finished show")

        # Determine which selector to use
        actual_selector = selector or self.selector

        # Ask the graph for the next scene
        next_id = self.graph.next_scene_id(
            self.state.scene_id,
            context=self.state.context,
            selector=actual_selector
        )

        if next_id is not None:
            log.debug("advancing from %s to %s", self.state.scene_id, next_id)
            self.state.scene_id = next_id
            return next_id
        else:
            # Show is over
            log.info("campaign %s finished after %d scene(s) on loop %d",
                     self.state.campaign, len(self.state.history), self.state.loop)
            self.state.finished = True
            self.state.scene_id = None
            return None

    def step(self) -> tuple[list, str | None]:
        """Play the current scene and advance to the next."""
        rendered = self.play_scene()
        next_id = self.advance()
        return (rendered, next_id)

    def run(self, max_scenes: int = 100) -> list[str]:
        """Run the campaign until finished or budget exhausted."""
        start = len(self.state.history)

        scenes_played = 0
        while not self.state.finished and scenes_played < max_scenes:
            self.step()
            scenes_played += 1

        # Return only the scenes played during this call
        played = self.state.history[start:]

        if scenes_played >= max_scenes and not self.state.finished:
            log.warning("run stopped by budget limit of %d scenes", max_scenes)

        return played

    def reset(self, keep_carry: bool = True) -> None:
        """Reset to the start of the loop."""
        self.state.scene_id = self.pack.start_scene
        self.state.finished = False
        self.state.history = []
        self.state.context = {}
        self.state.loop += 1

        if not keep_carry:
            self.state.carry = {}

        log.info("campaign %s reset to %s for loop %d (carry %s)",
                 self.state.campaign, self.state.scene_id, self.state.loop,
                 "kept" if keep_carry else "cleared")

    def save(self, path=None) -> None:
        """Save the current state to a file."""
        save_path = path or self.state_file
        if save_path is None:
            raise CampaignRuntimeError("no state file configured for saving")

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f)

    def load(self, path=None) -> None:
        """Load a saved state from a file."""
        load_path = path or self.state_file
        if load_path is None:
            raise CampaignRuntimeError("no state file configured for loading")

        try:
            with open(load_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("could not read campaign state from %s: %s", load_path, exc)
            raise CampaignRuntimeError(f"failed to load state from {load_path}") from exc

        # Validate the payload
        if not isinstance(data, dict):
            raise CampaignRuntimeError("state file does not contain a mapping")

        if "campaign" not in data:
            raise CampaignRuntimeError("state file missing 'campaign' key")

        if "scene_id" not in data:
            raise CampaignRuntimeError("state file missing 'scene_id' key")

        # Check campaign name
        if data["campaign"] != self.pack.name:
            raise CampaignRuntimeError(
                f"state file campaign {data['campaign']!r} does not match pack {self.pack.name!r}"
            )

        # Validate scene id if present
        if data["scene_id"] is not None and data["scene_id"] not in self.pack.scenes:
            raise CampaignRuntimeError(
                f"state file references unknown scene {data['scene_id']!r}"
            )

        # Load the state
        self.state = CampaignState.from_dict(data)
        log.info("resumed campaign %s at scene %s (loop %d, %d scene(s) played)",
                 self.state.campaign, self.state.scene_id, self.state.loop,
                 len(self.state.history))
