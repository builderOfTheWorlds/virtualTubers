"""
synth.py — tiny vectorized numpy voices for the synth-ambient score.

Everything is additive/analytic (no per-sample Python loops) so a whole bar
renders in a few numpy calls. "Brightness" is expressed as the number and
roll-off of harmonics rather than a filter, because a one-pole IIR needs a
per-sample recurrence that numpy can't vectorize.

All voices return MONO float32 arrays; engine.py pans and mixes them.
"""
import numpy as np

SAMPLE_RATE = 44100


def midi_to_hz(midi):
    return 440.0 * 2.0 ** ((np.asarray(midi, dtype=np.float64) - 69.0) / 12.0)


def _t(n, sr=SAMPLE_RATE):
    return np.arange(n, dtype=np.float64) / sr


def adsr(n, attack_s, release_s, sustain_s, sr=SAMPLE_RATE):
    """Linear attack, flat sustain, exponential-ish release, length n."""
    env = np.zeros(n, dtype=np.float64)
    a = max(1, min(n, int(attack_s * sr)))
    env[:a] = np.linspace(0.0, 1.0, a, endpoint=False)
    s_end = max(a, min(n, int((attack_s + sustain_s) * sr)))
    env[a:s_end] = 1.0
    r = n - s_end
    if r > 0:
        env[s_end:] = np.exp(-np.linspace(0.0, 5.0, r))
        env[s_end:] *= np.linspace(1.0, 0.0, r) ** 0.25  # force true zero at end
    return env


def additive(freq, n, harmonics, rolloff, detune_cents=0.0, phase=0.0,
             vibrato_hz=0.0, vibrato_cents=0.0, sr=SAMPLE_RATE):
    """Sum of `harmonics` partials with amplitude 1/k**rolloff.
    rolloff ~1 = saw-like (bright), ~2 = soft, large = near sine."""
    t = _t(n, sr)
    f = freq * 2.0 ** (detune_cents / 1200.0)
    if vibrato_hz and vibrato_cents:
        dev = (2.0 ** (vibrato_cents / 1200.0) - 1.0) * f
        inst_phase = 2 * np.pi * (f * t - dev / (2 * np.pi * vibrato_hz)
                                  * np.cos(2 * np.pi * vibrato_hz * t)) + phase
    else:
        inst_phase = 2 * np.pi * f * t + phase
    out = np.zeros(n, dtype=np.float64)
    nyquist = sr / 2.0
    total = 0.0
    for k in range(1, max(1, int(harmonics)) + 1):
        if f * k >= nyquist * 0.9:
            break
        amp = 1.0 / (k ** rolloff)
        out += amp * np.sin(k * inst_phase)
        total += amp
    return out / max(total, 1e-9)


def pad_note(midi, dur_s, brightness, sr=SAMPLE_RATE):
    """Slow-attack, three-voice detuned additive pad."""
    n = int((dur_s + 1.5) * sr)
    f = float(midi_to_hz(midi))
    harm = 2 + int(round(brightness * 8))
    roll = 2.2 - 1.2 * brightness
    body = sum(additive(f, n, harm, roll, detune_cents=d, phase=p, sr=sr)
               for d, p in ((-7.0, 0.0), (0.0, 1.3), (7.0, 2.1))) / 3.0
    return body * adsr(n, min(0.8, dur_s * 0.4), 1.5, max(0.0, dur_s - 0.4), sr)


def drone_note(midi, dur_s, sr=SAMPLE_RATE):
    n = int((dur_s + 2.0) * sr)
    f = float(midi_to_hz(midi))
    t = _t(n, sr)
    lfo = 0.75 + 0.25 * np.sin(2 * np.pi * 0.11 * t)
    body = 0.7 * np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 1.5 * t)
    return body * lfo * adsr(n, 1.0, 2.0, max(0.0, dur_s - 1.0), sr)


def bass_note(midi, dur_s, gate, sr=SAMPLE_RATE):
    held = max(0.05, dur_s * gate)
    n = int((held + 0.3) * sr)
    f = float(midi_to_hz(midi))
    body = np.tanh(1.6 * additive(f, n, 3, 2.0, sr=sr))
    return body * adsr(n, 0.01, 0.3, held, sr)


def pluck_note(midi, dur_s, brightness, sr=SAMPLE_RATE):
    """Arp voice: bright attack decaying to a soft sine (exp decay)."""
    n = int((dur_s + 0.6) * sr)
    f = float(midi_to_hz(midi))
    t = _t(n, sr)
    harm = 2 + int(round(brightness * 6))
    bright = additive(f, n, harm, 1.3, sr=sr)
    soft = np.sin(2 * np.pi * f * t)
    mix = np.exp(-t * 18.0)
    decay = np.exp(-t * (3.5 / max(dur_s + 0.2, 0.1)))
    attack = np.minimum(1.0, t / 0.004)
    return (mix * bright + (1 - mix) * soft) * decay * attack


def lead_note(midi, dur_s, gate, brightness, sr=SAMPLE_RATE):
    held = max(0.06, dur_s * gate)
    n = int((held + 0.8) * sr)
    f = float(midi_to_hz(midi))
    harm = 2 + int(round(brightness * 4))
    body = additive(f, n, harm, 2.4, vibrato_hz=5.0, vibrato_cents=8.0, sr=sr)
    return body * adsr(n, min(0.08, held * 0.3), 0.8, held, sr)


def kick(sr=SAMPLE_RATE):
    n = int(0.35 * sr)
    t = _t(n, sr)
    freq = 45.0 + 70.0 * np.exp(-t * 30.0)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    return np.sin(phase) * np.exp(-t * 9.0)


def hat(rng, sr=SAMPLE_RATE):
    n = int(0.06 * sr)
    t = _t(n, sr)
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0.0)  # crude high-pass
    return 0.2 * noise * np.exp(-t * 70.0)


def reverb_ir(seconds=2.2, seed=7, sr=SAMPLE_RATE):
    """Stereo impulse response: decaying noise with a darkened tail. Used by
    engine.py with FFT overlap-add, so reverb is O(n log n) per bar."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = _t(n, sr)
    env = np.exp(-t * (6.9 / seconds))
    ir = rng.standard_normal((n, 2)) * env[:, None]
    # darken: running mean over a short window, grows along the tail
    kernel = np.ones(8) / 8.0
    for c in range(2):
        ir[:, c] = np.convolve(ir[:, c], kernel, mode="same")
    ir[: int(0.012 * sr)] = 0.0  # pre-delay
    return (ir / np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True))).astype(np.float64)
