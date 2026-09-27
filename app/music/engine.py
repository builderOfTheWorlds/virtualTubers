"""
engine.py — the live theme-variation engine.

MusicEngine renders the score ONE BAR AT A TIME:

    engine = MusicEngine(theme)
    engine.set_mood("tension", intensity=0.6)
    bar = engine.render_bar()     # -> Bar(audio (n,2) float32, notes, ...)

Per bar it:
  1. advances any in-flight mood transition (continuous params glide over
     `transition_bars`; mode/chord changes land on the first downbeat),
  2. picks the chord from the theme's progression (re-voiced in the mood's
     mode),
  3. schedules layer notes — pad/drone/bass/arp/pulse follow the chord;
     `lead` states the theme's motif every other phrase, and ALWAYS on the
     phrase after a mood change, so viewers hear the familiar tune in its
     new emotional colour,
  4. synthesizes them (synth.py) into a buffer that carries note tails and
     reverb across the bar line (no clicks at bar boundaries).

Every note played is returned in `Bar.notes`, so recorder.py can write the
exact score to Postgres. Rendering is deterministic per (theme.seed, bar
index, mood): the same score + engine version reproduces the same audio.
"""
import logging
from dataclasses import dataclass, field

import numpy as np

from music import synth
from music.mood_map import (MODES, MoodParams, interpolate,
                            mood_params, normalize_mood)

log = logging.getLogger("music.engine")

ENGINE_VERSION = "1.0.0"

#: Stereo pan per layer (-1 left .. 1 right).
_PAN = {"pad": 0.0, "drone": 0.0, "bass": 0.0, "arp": 0.35, "lead": -0.2, "pulse": 0.1}
#: Base mix level per layer before the mood's layer weights.
_LEVEL = {"pad": 0.16, "drone": 0.14, "bass": 0.22, "arp": 0.10, "lead": 0.16, "pulse": 0.20}
_TAIL_S = 6.0
_MASTER = 0.55


@dataclass
class Bar:
    index: int
    start_s: float
    duration_s: float
    mood: str
    target_mood: str
    intensity: float
    params: MoodParams
    chord_degree: int
    motif: bool
    notes: list = field(default_factory=list)
    audio: "np.ndarray | None" = None


class MusicEngine:
    def __init__(self, theme, mood="neutral", intensity=0.5, sample_rate=synth.SAMPLE_RATE,
                 transition_bars=2, lead_every=2):
        log.debug("MusicEngine.__init__ theme=%s mood=%s intensity=%s", theme.name, mood, intensity)
        self.theme = theme
        self.sr = sample_rate
        self.transition_bars = max(1, int(transition_bars))
        self.lead_every = max(1, int(lead_every))
        self.bar_index = 0
        self.time_s = 0.0
        self._intensity = float(intensity)
        self._target = normalize_mood(mood)
        self._current = mood_params(self._target, self._intensity)
        self._from = self._current
        self._transition_pos = self.transition_bars  # == finished
        self._force_motif_phrase = True
        self._carry = np.zeros((0, 2), dtype=np.float64)
        self._ir = synth.reverb_ir(sr=self.sr)
        self._rev_carry = np.zeros((0, 2), dtype=np.float64)

    # ── control ────────────────────────────────────────────────────────────
    @property
    def mood(self):
        return self._target

    @property
    def intensity(self):
        return self._intensity

    def set_mood(self, mood, intensity=None, transition_bars=None):
        """Request a new mood. Takes effect from the NEXT bar. Returns True
        if anything changed."""
        mood = normalize_mood(mood)
        intensity = self._intensity if intensity is None else max(0.0, min(1.0, float(intensity)))
        if mood == self._target and abs(intensity - self._intensity) < 1e-6:
            log.debug("set_mood no-op mood=%s intensity=%.2f", mood, intensity)
            return False
        log.info("music mood change from=%s to=%s intensity=%.2f bar=%d",
                 self._target, mood, intensity, self.bar_index)
        if mood != self._target:
            self._force_motif_phrase = True
        self._from = self._current
        self._target = mood
        self._intensity = intensity
        self._transition_pos = 0
        if transition_bars is not None:
            self.transition_bars = max(1, int(transition_bars))
        return True

    # ── harmony helpers ────────────────────────────────────────────────────
    def _key_root(self, params):
        return 12 * (self.theme.base_octave + 1) + self.theme.tonic_pc + params.transpose

    @staticmethod
    def degree_to_semitone(degree, mode):
        """1-based scale degree (may be <1 or >7) -> semitones from tonic."""
        scale = MODES[mode]
        idx = degree - 1
        octave, step = divmod(idx, 7)
        return scale[step] + 12 * octave

    def chord_degrees(self, root_degree, ext):
        degs = [root_degree, root_degree + 2, root_degree + 4]
        if ext == 1:
            degs.append(root_degree + 6)
        elif ext == 2:
            degs.append(root_degree + 8)
        elif ext >= 3:
            degs += [root_degree + 1, root_degree + 6]
        return degs

    # ── scheduling ─────────────────────────────────────────────────────────
    def _advance_params(self):
        target = mood_params(self._target, self._intensity)
        if self._transition_pos < self.transition_bars:
            self._transition_pos += 1
            t = self._transition_pos / self.transition_bars
            self._current = interpolate(self._from, target, t)
        else:
            self._current = target
        return self._current

    def _motif_notes(self, params, phrase_bar, beats_per_bar, rng):
        """Lead notes for this bar: [(onset_beats, dur_beats, degree, octave)]."""
        start = phrase_bar * beats_per_bar
        end = start + beats_per_bar
        out, cursor = [], 0.0
        for note in self.theme.motif:
            dur = note.beats / max(params.motif_rate, 0.125)
            if start <= cursor < end and note.degree != 0:
                out.append((cursor - start, dur, note.degree, note.octave))
            cursor += dur
            if cursor >= end:
                break
        return out

    def _schedule(self, params, rng):
        bpb = self.theme.beats_per_bar
        prog = self.theme.progression
        phrase_len = len(prog)
        phrase_idx, phrase_bar = divmod(self.bar_index, phrase_len)
        root_deg = prog[phrase_bar]
        root = self._key_root(params)
        mode = params.mode

        if phrase_bar == 0:
            self._motif_phrase = (self._force_motif_phrase
                                  or phrase_idx % self.lead_every == 0)
            self._force_motif_phrase = False
        motif_phrase = getattr(self, "_motif_phrase", True)

        notes = []  # dicts: layer, midi, beat, beats, vel

        def add(layer, midi, beat, beats, vel):
            notes.append({"layer": layer, "midi": int(midi), "beat": round(float(beat), 4),
                          "beats": round(float(beats), 4), "vel": round(float(vel), 3)})

        chord = [root + self.degree_to_semitone(d, mode)
                 for d in self.chord_degrees(root_deg, params.chord_ext)]
        if params.pad > 0.01:
            for m in chord:
                add("pad", m, 0, bpb, params.pad)
        if params.drone > 0.01 and phrase_bar == 0:
            add("drone", root - 12, 0, bpb * phrase_len, params.drone)
        if params.bass > 0.01:
            bass_midi = chord[0] - 12
            if params.arousal > 0.3:
                for b in range(bpb * 2):
                    add("bass", bass_midi + (12 if b % 4 == 3 else 0), b / 2, 0.5,
                        params.bass * (1.0 if b % 2 == 0 else 0.7))
            elif params.arousal > -0.3:
                add("bass", bass_midi, 0, bpb / 2, params.bass)
                add("bass", bass_midi + self.degree_to_semitone(5, mode), bpb / 2, bpb / 2,
                    params.bass * 0.8)
            else:
                add("bass", bass_midi, 0, bpb, params.bass)
        if params.arp > 0.01:
            steps = bpb * params.arp_rate
            arp_tones = chord + [chord[0] + 12, chord[1] + 12]
            up = rng.random() < 0.5
            for s in range(steps):
                if s != 0 and rng.random() > params.density:
                    continue
                idx = s % len(arp_tones)
                m = arp_tones[idx if up else -1 - idx] + 12
                add("arp", m, s / params.arp_rate, 1 / params.arp_rate,
                    params.arp * (1.0 if s % params.arp_rate == 0 else 0.7))
        if params.pulse > 0.01:
            for b in range(bpb):
                add("pulse", 36, b, 0.25, params.pulse)          # kick
                add("pulse", 42, b + 0.5, 0.125, params.pulse * 0.6)  # hat
        if params.lead > 0.01 and motif_phrase:
            lead_root = root + 24
            for onset, dur, deg, octv in self._motif_notes(params, phrase_bar, bpb, rng):
                add("lead", lead_root + self.degree_to_semitone(deg, mode) + 12 * octv,
                    onset, dur, params.lead)
        return root_deg, motif_phrase, notes

    # ── synthesis ──────────────────────────────────────────────────────────
    def _voice(self, note, params, spb, rng):
        dur_s = note["beats"] * spb
        layer = note["layer"]
        if layer == "pad":
            return synth.pad_note(note["midi"], dur_s, params.brightness, self.sr)
        if layer == "drone":
            return synth.drone_note(note["midi"], dur_s, self.sr)
        if layer == "bass":
            return synth.bass_note(note["midi"], dur_s, params.gate, self.sr)
        if layer == "arp":
            return synth.pluck_note(note["midi"], dur_s * params.gate, params.brightness, self.sr)
        if layer == "lead":
            return synth.lead_note(note["midi"], dur_s, params.gate, params.brightness, self.sr)
        if layer == "pulse":
            return synth.kick(self.sr) if note["midi"] == 36 else synth.hat(rng, self.sr)
        raise ValueError(f"unknown layer {layer!r}")

    def _reverb(self, dry):
        """FFT overlap-add convolution; carries the tail into the next bar."""
        n = dry.shape[0]
        m = self._ir.shape[0]
        size = 1 << int(np.ceil(np.log2(n + m - 1)))
        wet = np.fft.irfft(np.fft.rfft(dry, size, axis=0) * np.fft.rfft(self._ir, size, axis=0),
                           size, axis=0)[: n + m - 1]
        if self._rev_carry.shape[0]:
            k = min(self._rev_carry.shape[0], wet.shape[0])
            wet[:k] += self._rev_carry[:k]
        self._rev_carry = wet[n:].copy()
        return wet[:n]

    def render_bar(self):
        """Render the next bar. Returns a Bar with float32 stereo audio."""
        params = self._advance_params()
        rng = np.random.default_rng([self.theme.seed, self.bar_index])
        spb = 60.0 / params.tempo_bpm
        bar_len = int(round(self.theme.beats_per_bar * spb * self.sr))
        root_deg, motif_phrase, notes = self._schedule(params, rng)
        voices = []
        need = bar_len + int(_TAIL_S * self.sr)
        for note in notes:
            wave = self._voice(note, params, spb, rng)
            start = int(round(note["beat"] * spb * self.sr))
            voices.append((note, start, wave))
            need = max(need, start + wave.size)
        need = max(need, self._carry.shape[0])
        buf = np.zeros((need, 2), dtype=np.float64)
        if self._carry.shape[0]:
            buf[: self._carry.shape[0]] += self._carry

        for note, start, wave in voices:
            if wave.size == 0:
                continue
            pan = _PAN[note["layer"]]
            gain = _LEVEL[note["layer"]] * note["vel"]
            # constant-power pan
            angle = (pan + 1.0) * np.pi / 4.0
            buf[start:start + wave.size, 0] += wave * gain * np.cos(angle) * 1.414
            buf[start:start + wave.size, 1] += wave * gain * np.sin(angle) * 1.414
            note["t"] = round(self.time_s + note["beat"] * spb, 4)
            note["dur_s"] = round(note["beats"] * spb, 4)
        dry = buf

        out = dry[:bar_len]
        self._carry = dry[bar_len:]
        # Only the carried-in dry tail + this bar's dry go into the reverb;
        # anything past bar_len is reverberated next bar when it lands.
        wet = self._reverb(out)
        mixed = out + params.reverb * 0.6 * wet
        audio = np.tanh(mixed * _MASTER * 1.6) / 1.6

        bar = Bar(index=self.bar_index, start_s=round(self.time_s, 4),
                  duration_s=round(bar_len / self.sr, 4), mood=params.mood,
                  target_mood=self._target, intensity=self._intensity, params=params,
                  chord_degree=root_deg, motif=motif_phrase, notes=notes,
                  audio=audio.astype(np.float32))
        log.debug("render_bar index=%d mood=%s notes=%d dur=%.2fs",
                  bar.index, bar.mood, len(notes), bar.duration_s)
        self.bar_index += 1
        self.time_s += bar_len / self.sr
        return bar

    def render_seconds(self, seconds):
        """Convenience for offline rendering: bars until >= `seconds`."""
        bars, total = [], 0.0
        while total < seconds:
            bar = self.render_bar()
            bars.append(bar)
            total += bar.duration_s
        return bars
