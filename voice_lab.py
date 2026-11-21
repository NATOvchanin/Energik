"""
Лаборатория голоса: проигрывает одну фразу разными пресетами, чтобы выбрать на слух.

python voice_lab.py                   все пресеты подряд
python voice_lab.py tenna             только один пресет
python voice_lab.py tenna "свой текст"
Сохранить в файлы (папка out/):  добавь --save
"""
import sys
import wave

import numpy as np

import config
from voice import Voice

PHRASE = ("Привет, Босс! Я Мистер Энергик, твой бортовой компьютер. "
          "Сегодня ты опять собираешься всё испортить? Отлично, я принесу попкорн.")


def save_wav(path, audio, sr):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    save = "--save" in sys.argv
    cfg = config.load()
    voice = Voice(cfg)
    names = [args[0]] if args else list(voice.presets)
    text = args[1] if len(args) > 1 else PHRASE
    raw = voice.synth(text)
    out_dir = config.ROOT / "out"
    if save:
        out_dir.mkdir(exist_ok=True)
    import effects
    for n in names:
        print(f"\n--- пресет: {n} ---")
        audio = effects.apply_preset(raw, voice.sr, voice.presets[n],
                                     voice.target_peak, voice.rng)
        if save:
            save_wav(out_dir / f"{n}.wav", audio, voice.sr)
        voice.play(audio)


if __name__ == "__main__":
    main()
