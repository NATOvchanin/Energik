"""Голос Энергика: Silero TTS (процессор) + роботизация + воспроизведение."""
import re
import time
import urllib.request

import numpy as np
import sounddevice as sd
import torch

import devices
import effects
from config import MODELS_DIR

try:
    from pedalboard import time_stretch
except ImportError:  # очень старая версия pedalboard
    time_stretch = None

MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
MODEL_PATH = MODELS_DIR / "silero_v4_ru.pt"


def _download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[ГОЛОС] Скачиваю голосовую модель (один раз, ~100 МБ)...")
    urllib.request.urlretrieve(url, path)


def clean_text(text: str) -> str:
    """Silero не читает цифры и ЗАГЛАВНЫЕ, поэтому подготавливаем текст."""
    t = text.strip()
    # слова ЗАГЛАВНЫМИ буквами (БОСС) превращаем в обычные
    t = re.sub(r"\b[А-ЯЁ]{3,}\b", lambda m: m.group().lower(), t)

    def num(m):
        try:
            from num2words import num2words
            return num2words(int(m.group()), lang="ru")
        except Exception:
            return m.group()

    t = re.sub(r"\d+", num, t)
    t = re.sub(r"[*_#`~<>|\\]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def split_sentences(text: str, limit: int = 300) -> list[str]:
    """Режем на предложения; слишком длинные ещё и по пробелам (Silero не любит простыни)."""
    out = []
    for p in re.split(r"(?<=[.!?…])\s+", text):
        p = p.strip()
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            if cut < 50:
                cut = limit
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


class Voice:
    def __init__(self, cfg: dict):
        v = cfg["voice"]
        self.speaker = v["speaker"]
        self.sr = int(v["sample_rate"])
        self.target_peak = float(v.get("target_peak", 0.7))
        self.presets = v["presets"]
        self.preset_name = v["preset"]
        self.out_dev = devices.find(cfg["audio"]["output_device"], "output")
        self.rng = np.random.default_rng()
        self.speed = float(v.get("speed", self.presets.get(self.preset_name, {}).get("speed", 1.0)))      # 1.0 = как есть, 1.08 = чуть быстрее
        self.tail_s = float(v.get("tail_ms", self.presets.get(self.preset_name, {}).get("tail_ms", 400))) / 1000
        self._stream = None

        torch.set_num_threads(int(v.get("threads", 3)))
        self.device = v.get("device", "cpu")
        if self.device == "cuda" and not torch.cuda.is_available():
            print("[ГОЛОС] CUDA недоступна для torch, голос будет на процессоре.")
            self.device = "cpu"
        if not MODEL_PATH.exists():
            _download(MODEL_URL, MODEL_PATH)
        print("[ГОЛОС] Загружаю Silero...")
        importer = torch.package.PackageImporter(str(MODEL_PATH))
        self.model = importer.load_pickle("tts_models", "model")
        self.model.to(self.device)
        print(f"[ГОЛОС] Готово. Голос: {self.speaker}, пресет: {self.preset_name}, "
              f"работает на: {self.device}")

    def synth(self, text: str) -> np.ndarray:
        """Чистая речь без эффектов."""
        chunks = []
        pause = np.zeros(int(0.12 * self.sr), dtype=np.float32)
        for s in split_sentences(clean_text(text)):
            try:
                wav = self.model.apply_tts(text=s, speaker=self.speaker,
                                           sample_rate=self.sr,
                                           put_accent=True, put_yo=True)
            except Exception as e:  # например, фраза без русских букв
                print(f"[ГОЛОС] Пропускаю фрагмент '{s[:30]}': {e}")
                continue
            chunks.append(wav.detach().cpu().numpy().astype(np.float32))
            chunks.append(pause)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    def render(self, text: str, preset: str | None = None) -> np.ndarray:
        raw = self.synth(text)
        if len(raw) == 0:
            return raw
        if time_stretch is not None and abs(self.speed - 1.0) > 0.01:
            try:
                raw = time_stretch(raw.reshape(1, -1), self.sr,
                                   stretch_factor=self.speed)[0].astype(np.float32)
            except Exception as e:
                print(f"[ГОЛОС] не удалось изменить скорость: {e}")
                self.speed = 1.0
        p = self.presets[preset or self.preset_name]
        return effects.apply_preset(raw, self.sr, p, self.target_peak, self.rng)

    def _open_stream(self):
        if self._stream is None:
            self._stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32",
                                           device=self.out_dev, latency="high")
            self._stream.start()
        return self._stream

    def play(self, audio: np.ndarray) -> None:
        """Пишем в один постоянно открытый поток: фразы идут встык и не обрезаются."""
        if len(audio) == 0:
            return
        try:
            self._open_stream().write(np.ascontiguousarray(audio.reshape(-1, 1)))
        except Exception as e:
            print(f"[ГОЛОС] поток не сработал ({e}), играю запасным способом")
            self._stream = None
            sd.play(audio, self.sr, device=self.out_dev)
            sd.wait()

    def drain(self) -> None:
        """Дослушать хвост: тишина в конце выталкивает последнее слово из буфера (Bluetooth!)."""
        if self._stream is None:
            return
        pad = max(self.tail_s, float(self._stream.latency) * 1.5 + 0.1)
        try:
            self._stream.write(np.zeros((int(pad * self.sr), 1), dtype=np.float32))
        except Exception:
            pass

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def speak(self, text: str, preset: str | None = None) -> dict:
        t0 = time.time()
        audio = self.render(text, preset)
        t1 = time.time()
        print(f"Энергик: {text}")
        self.play(audio)
        return {"render_s": t1 - t0, "audio_s": len(audio) / self.sr}
