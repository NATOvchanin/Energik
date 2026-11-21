"""
Настройщик голоса Энергика: один голос, ползунки, звук сразу на слух.

Запуск (из папки проекта):  python voice_tuner.py

- Двигай ползунок и отпускай: фраза проиграется заново (если стоит галочка «Автоплей»).
- «Играть» проигрывает текущий вариант, «Стоп» обрывает звук.
- «Сохранить» записывает пресет в файл my_voice.toml и копирует его в буфер обмена.
  Содержимое можно вставить в config.toml и выставить preset = "my_voice".
"""
import threading
import tkinter as tk
from tkinter import ttk

import config
import effects
from voice import Voice

START_PRESET = "soft_robot"
DEFAULT_TEXT = ("Привет, Босс! Я Мистер Энергик, твой бортовой компьютер. "
                "Сегодня ты опять собираешься всё испортить? Отлично, я принесу попкорн.")
SPEAKERS = ["aidar", "eugene", "baya", "kseniya", "xenia"]

# (ключ, подпись, от, до, шаг, подсказка)
SLIDERS = [
    ("pitch_semitones", "Высота тона (полутоны)", -8, 8, 0.25, "минус = ниже и грубее, плюс = выше"),
    ("ring_freq", "Металл: частота (Гц)", 0, 200, 5, "30 = далековская дрожь, 70-100 = мягче"),
    ("ring_depth", "Металл: глубина", 0, 1, 0.05, "0 = выкл., больше = грубее и менее разборчиво"),
    ("resample_hz", "Зернистость (Гц)", 0, 24000, 500, "ниже = грубее; 0 или 24000 = выкл."),
    ("comb_ms", "Жесть: задержка (мс)", 0, 20, 0.5, "короткая задержка = «труба»"),
    ("comb_feedback", "Жесть: обратная связь", 0, 0.9, 0.05, "больше = звонче"),
    ("comb_mix", "Жесть: доля", 0, 1, 0.05, "0 = выкл."),
    ("highpass_hz", "Срез низов (Гц)", 0, 400, 10, "убирает гул; 0 = выкл."),
    ("lowpass_hz", "Срез верхов (Гц)", 0, 12000, 250, "главная защита ушей; 0 = выкл."),
    ("wet", "Робот / чистый голос", 0, 1, 0.05, "1 = только робот, 0 = чистый голос"),
    ("stutter_chance", "Шанс заедания", 0, 1, 0.05, "на каждое проигрывание"),
    ("target_peak", "Громкость", 0.1, 0.95, 0.05, "общая громкость"),
]


def clean_params(p: dict) -> dict:
    """Превращает «выключенные» положения ползунков в честные нули."""
    q = dict(p)
    if q["resample_hz"] < 3000 or q["resample_hz"] >= 24000:
        q["resample_hz"] = 0
    return q


def make_toml(p: dict, speaker: str) -> str:
    lines = ["# speaker = \"%s\"  (впиши в [voice] в config.toml)" % speaker,
             "[voice.presets.my_voice]"]
    for key, *_ in SLIDERS:
        if key == "target_peak":
            continue
        v = p[key]
        lines.append(f"{key} = {v:g}")
    lines.append("")
    lines.append(f"# target_peak = {p['target_peak']:g}  (впиши в [voice] в config.toml)")
    return "\n".join(lines) + "\n"


class Tuner:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Настройка голоса Энергика")
        self.status = tk.StringVar(value="Загружаю голос...")
        ttk.Label(root, textvariable=self.status).pack(anchor="w", padx=10, pady=(8, 0))
        root.update()

        cfg = config.load()
        self.voice = Voice(cfg)
        start = cfg["voice"]["presets"][START_PRESET]
        self.raw_cache: dict = {}
        self.busy = False

        # --- верх: голос и текст ---
        top = ttk.Frame(root)
        top.pack(fill="x", padx=10, pady=6)
        ttk.Label(top, text="Исходный голос:").pack(side="left")
        self.speaker = tk.StringVar(value=cfg["voice"]["speaker"])
        cb = ttk.Combobox(top, textvariable=self.speaker, values=SPEAKERS,
                          width=10, state="readonly")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.maybe_play())
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Автоплей при отпускании", variable=self.auto).pack(side="left", padx=12)

        self.text = tk.StringVar(value=DEFAULT_TEXT)
        entry = ttk.Entry(root, textvariable=self.text)
        entry.pack(fill="x", padx=10)
        entry.bind("<Return>", lambda e: self.play())

        # --- ползунки ---
        self.vars: dict[str, tk.DoubleVar] = {}
        self.defaults = {}
        frame = ttk.Frame(root)
        frame.pack(fill="both", padx=10, pady=8)
        for row, (key, label, lo, hi, step, hint) in enumerate(SLIDERS):
            default = (cfg["voice"].get("target_peak", 0.7) if key == "target_peak"
                       else start.get(key, 0))
            self.defaults[key] = default
            var = tk.DoubleVar(value=default)
            self.vars[key] = var
            ttk.Label(frame, text=label, width=26).grid(row=row, column=0, sticky="w")
            sc = tk.Scale(frame, from_=lo, to=hi, resolution=step, orient="horizontal",
                          length=380, variable=var)
            sc.grid(row=row, column=1, padx=6)
            sc.bind("<ButtonRelease-1>", lambda e: self.maybe_play())
            ttk.Label(frame, text=hint, foreground="#666").grid(row=row, column=2, sticky="w")

        # --- кнопки ---
        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=10, pady=8)
        ttk.Button(btns, text="Играть", command=self.play).pack(side="left")
        ttk.Button(btns, text="Стоп", command=self.stop).pack(side="left", padx=6)
        ttk.Button(btns, text="Сбросить", command=self.reset).pack(side="left")
        ttk.Button(btns, text="Сохранить в my_voice.toml", command=self.save).pack(side="right")
        self.status.set("Готово. Двигай ползунки.")

    # ---------- логика ----------
    def params(self) -> dict:
        return clean_params({k: v.get() for k, v in self.vars.items()})

    def maybe_play(self):
        if self.auto.get():
            self.play()

    def play(self):
        if self.busy:
            return
        self.busy = True
        p = self.params()
        speaker = self.speaker.get()
        text = self.text.get().strip()
        self.status.set("Синтезирую...")
        threading.Thread(target=self._work, args=(p, speaker, text), daemon=True).start()

    def _work(self, p, speaker, text):
        try:
            import sounddevice as sd
            key = (speaker, text)
            if key not in self.raw_cache:
                self.voice.speaker = speaker
                self.raw_cache[key] = self.voice.synth(text)
            raw = self.raw_cache[key]
            audio = effects.apply_preset(raw, self.voice.sr, p, p["target_peak"],
                                         self.voice.rng)
            self.root.after(0, lambda: self.status.set("Играет..."))
            sd.stop()
            sd.play(audio, self.voice.sr, device=self.voice.out_dev)
            sd.wait()
            self.root.after(0, lambda: self.status.set("Готово."))
        except Exception as e:
            msg = f"Ошибка: {e}"
            self.root.after(0, lambda: self.status.set(msg))
        finally:
            self.busy = False

    def stop(self):
        import sounddevice as sd
        sd.stop()
        self.status.set("Остановлено.")

    def reset(self):
        for k, v in self.vars.items():
            v.set(self.defaults[k])
        self.maybe_play()

    def save(self):
        p = self.params()
        toml = make_toml(p, self.speaker.get())
        (config.ROOT / "my_voice.toml").write_text(toml, encoding="utf-8")
        self.root.clipboard_clear()
        self.root.clipboard_append(toml)
        self.status.set("Сохранено в my_voice.toml и скопировано в буфер обмена.")


def main():
    root = tk.Tk()
    Tuner(root)
    root.mainloop()


if __name__ == "__main__":
    main()
