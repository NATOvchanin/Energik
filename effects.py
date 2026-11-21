"""Роботизация голоса: чистая речь Silero -> цепочка эффектов -> безопасная громкость."""
import numpy as np
from pedalboard import (Compressor, Delay, HighpassFilter, Limiter,
                        LowpassFilter, Pedalboard, PitchShift, Resample)


def _board(x: np.ndarray, sr: int, plugins: list) -> np.ndarray:
    if not plugins:
        return x
    out = Pedalboard(plugins)(x.reshape(1, -1).astype(np.float32), sr)
    return out[0]


def ring_mod(x: np.ndarray, sr: int, freq: float, depth: float) -> np.ndarray:
    """Кольцевая модуляция: металлический призвук. depth 0..1."""
    if depth <= 0 or freq <= 0:
        return x
    t = np.arange(len(x), dtype=np.float32) / sr
    carrier = np.sin(2 * np.pi * freq * t).astype(np.float32)
    return ((1 - depth) * x + depth * x * carrier).astype(np.float32)


def stutter(x: np.ndarray, sr: int, chance: float, rng) -> np.ndarray:
    """Короткое «заедание»: повторяет кусочек звука 2-3 раза."""
    if chance <= 0 or rng.random() > chance or len(x) < sr // 2:
        return x
    seg_len = int(rng.uniform(0.06, 0.12) * sr)
    start = int(rng.uniform(0.1, 0.6) * len(x))
    seg = x[start:start + seg_len].copy()
    if len(seg) < 100:
        return x
    fade = min(int(0.005 * sr), len(seg) // 4)
    seg[:fade] *= np.linspace(0, 1, fade)
    seg[-fade:] *= np.linspace(1, 0, fade)
    reps = int(rng.integers(2, 4))
    return np.concatenate([x[:start + seg_len]] + [seg] * reps + [x[start + seg_len:]])


def apply_preset(audio: np.ndarray, sr: int, p: dict, target_peak: float = 0.7,
                 rng=None) -> np.ndarray:
    rng = rng or np.random.default_rng()
    dry = audio.astype(np.float32)
    x = stutter(dry, sr, p.get("stutter_chance", 0.0), rng)
    dry = x  # сухой сигнал тоже со «заеданием», чтобы смешивание не поплыло

    wet_amount = float(p.get("wet", 1.0))
    if wet_amount > 0:
        y = x
        if p.get("pitch_semitones", 0):
            y = _board(y, sr, [PitchShift(semitones=float(p["pitch_semitones"]))])
        y = ring_mod(y, sr, p.get("ring_freq", 0), p.get("ring_depth", 0))

        plugins = []
        if p.get("resample_hz", 0):
            plugins.append(Resample(target_sample_rate=float(p["resample_hz"])))
        if p.get("comb_ms", 0) and p.get("comb_mix", 0) > 0:
            plugins.append(Delay(delay_seconds=p["comb_ms"] / 1000.0,
                                 feedback=p.get("comb_feedback", 0.3),
                                 mix=p["comb_mix"]))
        y = _board(y, sr, plugins)

        n = min(len(y), len(dry))
        out = dry[:n] * (1 - wet_amount) + y[:n] * wet_amount
    else:
        out = dry

    # финальная «защита ушей»
    tail = []
    if p.get("highpass_hz", 0):
        tail.append(HighpassFilter(cutoff_frequency_hz=float(p["highpass_hz"])))
    if p.get("lowpass_hz", 0):
        tail.append(LowpassFilter(cutoff_frequency_hz=float(p["lowpass_hz"])))
    tail.append(Compressor(threshold_db=-18, ratio=3, attack_ms=5, release_ms=80))
    tail.append(Limiter(threshold_db=-3))
    out = _board(out, sr, tail)

    peak = float(np.max(np.abs(out))) or 1.0
    out = out * (target_peak / peak)
    fade = int(0.01 * sr)
    if len(out) > 2 * fade:
        out[:fade] *= np.linspace(0, 1, fade)
        out[-fade:] *= np.linspace(1, 0, fade)
    return out.astype(np.float32)
