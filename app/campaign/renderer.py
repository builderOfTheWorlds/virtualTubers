"""Render scene beats into a performance an audience sees and hears."""
import logging
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from campaign.pack import Beat, CampaignPack, PackError
from campaign.primitives import render as primitive_render, PrimitiveError
from replay import DIALOGUE_CPS, Pacer, Palette

from agent_state import write_state

log = logging.getLogger(__name__)


class RendererError(RuntimeError):
    """Raised for a beat the renderer cannot perform at all."""
    pass


@dataclass
class RenderedBeat:
    """Record of what was rendered for a beat."""
    beat: Beat
    kind: str
    speaker: str | None
    text: str = ""
    audio: object = None


class SceneRenderer:
    """Turns scene beats into a performance an audience sees and hears."""

    def __init__(self, pack: CampaignPack, out=None, pacer=None, palette=None,
                 state_path=None, tts=None, audio_dir=None,
                 audio_player=None, pane_control=None, improviser=None):
        self.pack = pack
        self.out = out or sys.stdout
        self.pacer = pacer or Pacer()
        self.palette = palette or Palette()
        self.state_path = state_path
        self.tts = tts
        # Resolved lazily on first synthesis: a dry run never speaks, and must
        # not leave a temp directory behind for audio it will never write.
        self.audio_dir = Path(audio_dir) if audio_dir else None
        self.audio_player = audio_player
        self.pane_control = pane_control
        self.improviser = improviser

        if self.audio_dir is not None:
            self.audio_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_audio_dir(self) -> Path:
        if self.audio_dir is None:
            self.audio_dir = Path(tempfile.mkdtemp(prefix="campaign-audio-"))
        return self.audio_dir

    def render_beat(self, beat: Beat) -> RenderedBeat:
        """Render one beat into the performance."""
        log.debug("rendering beat %s", beat.kind)
        
        # Resolve speaker if present
        cast_member = None
        if beat.speaker is not None:
            try:
                cast_member = self.pack.member(beat.speaker)
            except PackError as exc:
                raise RendererError(f"unknown speaker {beat.speaker!r}") from exc

        # Handle improv seam
        text = beat.text
        if beat.improv and self.improviser and cast_member:
            try:
                improvised = self.improviser(beat, cast_member)
                if improvised and improvised.strip():
                    text = improvised
            except Exception as exc:
                log.warning("improviser failed for %s: %s", beat.speaker, exc)
                # Fall back to scripted text

        # Determine line to write and text to speak
        line = ""
        cps = DIALOGUE_CPS
        color = ""

        if beat.kind == "narration":
            line = text
            cps = DIALOGUE_CPS * 2
            color = self.palette.dim
            rendered_text = text

        elif beat.kind == "dialogue":
            # The label is display only — `text` stays the bare spoken line so
            # no character announces their own name before every sentence.
            if cast_member:
                line = f"{self.palette.cyan}{cast_member.name}{self.palette.reset}: {text}"
            else:
                line = text
            rendered_text = text


        elif beat.kind == "action":
            if not cast_member:
                raise RendererError(f"action beat must have a speaker, got {beat.speaker!r}")
            
            # Check primitive is enabled
            if beat.primitive not in self.pack.primitives:
                raise RendererError(f"primitive {beat.primitive!r} not enabled for this pack")
                
            # Render the primitive
            try:
                rendered_text = primitive_render(beat.primitive, cast_member.name, beat.params)
            except PrimitiveError as exc:
                raise RendererError(str(exc)) from exc
                
            line = rendered_text
            color = self.palette.yellow

        elif beat.kind == "pane":
            # Pane beats produce no output text
            rendered_text = ""
            
        else:
            raise RendererError(f"unknown beat kind {beat.kind!r}")

        # Emit the line with pacing and coloring
        if beat.kind != "pane":
            self._emit(line, cps, color)

        # Handle avatar state
        if self.state_path and rendered_text:
            expression = "neutral"
            if beat.kind == "dialogue":
                expression = "talking"
            elif beat.kind == "action":
                expression = "focused"
                
            write_state(self.state_path, expression, "", rendered_text)

        # Handle voice
        audio = None
        if self.tts and beat.kind != "pane" and rendered_text:
            try:
                wav_file = self._resolve_audio_dir() / f"{uuid.uuid4()}.wav"
                narration_obj = self.tts.synthesize(rendered_text, str(wav_file), speaker=beat.speaker)
                audio = narration_obj
                if self.audio_player:
                    self.audio_player(str(wav_file))
            except Exception as exc:
                log.warning("TTS failed for beat %s: %s", beat.kind, exc)

        # Handle pane control
        if beat.kind == "pane" and self.pane_control:
            try:
                self.pane_control(beat.show)
            except Exception as exc:
                log.warning("pane control failed for %s: %s", beat.show, exc)

        return RenderedBeat(
            beat=beat,
            kind=beat.kind,
            speaker=beat.speaker,
            text=rendered_text,
            audio=audio
        )

    def render_scene(self, scene, context=None) -> list[RenderedBeat]:
        """Render all beats in a scene."""
        results = []
        
        # Render enter narration if present
        if scene.enter_narration:
            enter_beat = Beat(kind="narration", speaker=self.pack.gm_id, text=scene.enter_narration)
            result = self.render_beat(enter_beat)
            results.append(result)
            
        # Render all beats in order
        for beat in scene.beats:
            result = self.render_beat(beat)
            results.append(result)
            
        return results

    def _emit(self, text, cps, color=""):
        """Emit text with pacing and coloring."""
        if text is not None:
            self.pacer.type_out(self.out.write, color + text + self.palette.reset, cps)
            self.out.write("\n")
