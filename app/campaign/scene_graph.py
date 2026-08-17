"""Walk the campaign's scene graph to determine what happens next."""
import logging
import random

from campaign.pack import Branch, Scene

log = logging.getLogger(__name__)

WEIGHT_KEY = "weight"


class SceneGraphError(ValueError):
    """Raised for an unknown scene id, an edge pointing at a scene that does
    not exist, or a selector naming a branch the scene does not have.
    """
    pass


class BranchSelector:
    """The pluggable seam for choosing which branch to take."""

    def select(self, scene: Scene, branches: list[Branch], context: dict) -> str | None:
        """Select a branch id from the given list, or None to abstain.

        Args:
            scene: The scene containing the branches.
            branches: Non-empty list of Branch objects.
            context: Runtime information about the show.

        Returns:
            The id of the chosen branch, or None to fall back to default_next.
        """
        raise NotImplementedError("BranchSelector is an interface")


class ScriptedSelector(BranchSelector):
    """Deterministic branch selection. Returns the first matching branch."""

    def select(self, scene: Scene, branches: list[Branch], context: dict) -> str | None:
        for branch in branches:
            if _branch_matches(branch, context):
                log.debug("scripted selector chose branch %s", branch.id)
                return branch.id
        return None


class ForcedSelector(BranchSelector):
    """A selector that forces a specific branch id."""

    def __init__(self, branch_id: str):
        self.branch_id = branch_id
        self._fallback = ScriptedSelector()

    def select(self, scene: Scene, branches: list[Branch], context: dict) -> str | None:
        if any(branch.id == self.branch_id for branch in branches):
            log.debug("forced selector chose branch %s", self.branch_id)
            return self.branch_id
        # This scene has no such branch, so forcing it means nothing here.
        return self._fallback.select(scene, branches, context)


class WeightedRandomSelector(BranchSelector):
    """Random branch selection weighted by branch.when['weight']."""

    def __init__(self, seed=None):
        self._rng = random.Random(seed)

    def select(self, scene: Scene, branches: list[Branch], context: dict) -> str | None:
        # Filter out branches with weight 0
        eligible = [b for b in branches if b.when.get(WEIGHT_KEY, 1) > 0]
        if not eligible:
            return None

        weights = [b.when.get(WEIGHT_KEY, 1) for b in eligible]
        chosen = self._rng.choices(eligible, weights=weights)[0]
        log.debug("weighted random selector chose branch %s", chosen.id)
        return chosen.id


class SceneGraph:
    """Walk the campaign's scene graph to determine what happens next."""

    def __init__(self, pack, selector=None):
        self.pack = pack
        self.selector = selector or ScriptedSelector()
        # Validate start scene exists
        if self.pack.start_scene not in self.pack.scenes:
            raise SceneGraphError(f"start scene {self.pack.start_scene!r} not found among scenes")

    @property
    def start(self) -> Scene:
        return self.pack.scenes[self.pack.start_scene]

    def successors(self, scene_id: str) -> list[str]:
        """Get all scene ids this scene can lead to."""
        if scene_id not in self.pack.scenes:
            raise SceneGraphError(f"unknown scene {scene_id!r}")
        
        scene = self.pack.scenes[scene_id]
        targets = []
        seen = set()
        
        # Add branch targets
        for branch in scene.branches:
            if branch.next not in seen:
                targets.append(branch.next)
                seen.add(branch.next)
        
        # Add default_next if it exists and is not already added
        if scene.default_next and scene.default_next not in seen:
            targets.append(scene.default_next)
            
        return targets

    def next_scene_id(self, scene_id: str, context=None, selector=None) -> str | None:
        """Resolve one step in the graph."""
        if context is None:
            context = {}
        
        # Step 1: Look up the scene
        if scene_id not in self.pack.scenes:
            raise SceneGraphError(f"unknown scene {scene_id!r}")
            
        scene = self.pack.scenes[scene_id]
        
        # Step 2: If no branches, skip selector and go to step 4
        if not scene.branches:
            next_id = scene.default_next
        else:
            # Step 3: Call the selector
            actual_selector = selector or self.selector
            chosen_branch_id = actual_selector.select(scene, scene.branches, context)
            
            # If selector returned a branch id, validate it exists on this scene
            if chosen_branch_id is not None:
                branch = next((b for b in scene.branches if b.id == chosen_branch_id), None)
                if branch is None:
                    raise SceneGraphError(
                        f"branch {chosen_branch_id!r} not found on scene {scene_id!r}")
                next_id = branch.next
            else:
                # Step 4: Fall back to default_next
                next_id = scene.default_next
                
        # Step 5: Validate the target exists
        if next_id is not None and next_id not in self.pack.scenes:
            raise SceneGraphError(f"next scene {next_id!r} not found among scenes")
            
        log.debug("resolved step from %s to %s", scene_id, next_id)
        return next_id

    def walk(self, start=None, context=None, selector=None, max_steps=1000):
        """A lazy iterator yielding Scene objects."""
        if start is None:
            start = self.pack.start_scene
            
        current_id = start
        steps = 0
        
        # Validate start scene exists
        if current_id not in self.pack.scenes:
            raise SceneGraphError(f"unknown scene {current_id!r}")
            
        while steps < max_steps:
            scene = self.pack.scenes[current_id]
            yield scene
            steps += 1
            
            # Get next scene id
            next_id = self.next_scene_id(current_id, context, selector)
            if next_id is None:
                break
                
            current_id = next_id


def _branch_matches(branch: Branch, context: dict) -> bool:
    """Check if a branch's conditions are met by the context."""
    for key, value in branch.when.items():
        if key == WEIGHT_KEY:
            continue  # Skip weight key for matching
        if key not in context or context[key] != value:
            return False
    return True
