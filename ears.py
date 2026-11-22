"""Уши Энергика: микрофон -> детектор речи (Silero VAD) -> Whisper -> текст."""
import queue
import re
import time
from collections import deque

import numpy as np
import sounddevice as sd
import torch

import devices

SR = 16000
CHUNK = 512  # Silero VAD требует ровно 512 отсчётов при 16 кГц (32 мс)
CHUNK_MS = CHUNK * 1000 / SR


# Слова, на которых люди обычно не заканчивают мысль: после них стоит подождать продолжения.
OPEN_WORDS = {"и", "а", "но", "или", "что", "чтобы", "потому", "если", "когда", "как", "типа", "ну",
              "вот", "это", "то", "в", "на", "с", "по", "для", "про", "у", "к", "о", "от", "из", "за",
              "я", "мы", "ты", "он", "она", "мне", "тебе", "меня", "не", "ни", "же", "бы", "ли", "так",
              "ещё", "еще", "вообще", "короче", "значит", "например", "просто", "блин", "чё", "че"}


def patience_for(text, patience_ms: int, soft_ms: int) -> int:
    """Сколько ещё подождать тишины, прежде чем считать мысль законченной (0 = можно отвечать)."""
    if not text:
        return 0
    t = text.strip()
    if t.endswith(("...", "…", ",", ":", ";", "-", "—")):
        return patience_ms                      # явно оборвано на полуслове
    if t.endswith((".", "!", "?")):
        return 0                                # законченное предложение
    words = re.findall(r"[\w'-]+", t.lower())
    if words and words[-1] in OPEN_WORDS:
        return patience_ms                      # «...и», «...потому что»
    return soft_ms                              # знаков нет: чуть подождём


class Ears:
    def __init__(self, cfg: dict):
        e = cfg["ears"]
        self.cfg = e
        self.lang = e["language"]
        self.thr = float(e["vad_threshold"])
        self.sil_chunks = int(e["min_silence_ms"] / CHUNK_MS)
        self.min_speech_chunks = int(e["min_speech_ms"] / CHUNK_MS)
        self.max_chunks = int(e["max_phrase_s"] * 1000 / CHUNK_MS)
        self.pre_chunks = int(e["preroll_ms"] / CHUNK_MS)
        self.halluc = [h.lower() for h in e["hallucinations"]]
        self.last_asr_s = 0.0
        self.patience_ms = int(e.get("patience_ms", 1500))
        self.patience_soft_ms = int(e.get("patience_soft_ms", 600))

        print("[УШИ] Загружаю детектор речи...")
        from silero_vad import load_silero_vad
        self.vad = load_silero_vad()

        self.model, desc = self._load_whisper(e)
        print(f"[УШИ] Whisper готов: {desc}")

        in_dev = devices.find(cfg["audio"]["input_device"], "input")
        self.q: queue.Queue = queue.Queue()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                     blocksize=CHUNK, device=in_dev,
                                     callback=self._on_audio)
        self.stream.start()
        name = sd.query_devices(in_dev if in_dev is not None else sd.default.device[0])["name"]
        print(f"[УШИ] Микрофон: {name}")

    # ---------- загрузка Whisper ----------
    def _load_whisper(self, e):
        from faster_whisper import WhisperModel
        name, dev, ct = e["whisper_model"], e["device"], e["compute_type"]
        if dev == "cpu" and "float16" in ct:
            ct = "int8"
        try:
            print(f"[УШИ] Загружаю Whisper {name} ({dev})... при первом запуске это скачивание.")
            m = WhisperModel(name, device=dev, compute_type=ct)
            # Прогрев: ошибки CUDA вылезают только при первом распознавании
            segs, _ = m.transcribe(np.zeros(SR, dtype=np.float32), language=self.lang)
            list(segs)
            return m, f"{name} / {dev} / {ct}"
        except Exception as ex:
            print(f"[УШИ] Не получилось на '{dev}': {ex}")
            print("[УШИ] Перехожу на процессор и модель small (медленнее, но работает).")
            m = WhisperModel("small", device="cpu", compute_type="int8")
            return m, "small / cpu / int8 (запасной вариант)"

    # ---------- микрофон ----------
    def _on_audio(self, indata, frames, time_info, status):
        self.q.put(indata[:, 0].copy())

    def flush(self) -> None:
        """Выбросить накопленный звук (например, пока Энергик говорил)."""
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    # ---------- главный метод ----------
    def listen(self) -> str | None:
        """
        Ждёт фразу, возвращает текст. None = шум/пустота/галлюцинация.
        Конец фразы определяется в два шага: пауза 0.7 с -> быстро распознаём -> если мысль закончена
        (точка, вопрос), отвечаем сразу; если оборвана («...и», запятая), ждём ещё и дослушиваем.
        """
        preroll = deque(maxlen=self.pre_chunks)
        speech: list[np.ndarray] = []
        in_speech = False
        voiced = 0
        silence = 0
        pending = None            # (текст, во сколько чанков тишины закончить ожидание)
        self.vad.reset_states()

        while True:
            chunk = self.q.get()
            with torch.no_grad():
                prob = self.vad(torch.from_numpy(chunk), SR).item()
            if not in_speech:
                preroll.append(chunk)
                if prob >= self.thr:
                    in_speech = True
                    speech = list(preroll)
                    voiced = 1
                    silence = 0
                continue

            speech.append(chunk)
            if prob >= self.thr:
                voiced += 1
                silence = 0
                pending = None        # человек продолжил говорить
            else:
                silence += 1
            if len(speech) >= self.max_chunks:
                break
            if silence >= self.sil_chunks:
                if voiced < self.min_speech_chunks:
                    return None
                if pending is None:
                    t0 = time.time()
                    text = self._transcribe(np.concatenate(speech))
                    self.last_asr_s = time.time() - t0
                    wait_ms = patience_for(text, self.patience_ms, self.patience_soft_ms)
                    if wait_ms == 0:
                        return text
                    print(f"   (мысль не закончена, жду продолжения: «...{text[-25:]}»)")
                    pending = (text, silence + int(wait_ms / CHUNK_MS))
                elif silence >= pending[1]:
                    return pending[0]

        if voiced < self.min_speech_chunks:
            return None
        t0 = time.time()
        text = self._transcribe(np.concatenate(speech))
        self.last_asr_s = time.time() - t0
        return text

    def _transcribe(self, audio: np.ndarray) -> str | None:
        segments, _ = self.model.transcribe(
            audio, language=self.lang, beam_size=1, vad_filter=False,
            condition_on_previous_text=False, temperature=0.0)
        parts = []
        for s in segments:
            if s.no_speech_prob > 0.8 and s.avg_logprob < -1.0:
                continue
            parts.append(s.text)
        text = " ".join(parts).strip()
        low = text.lower()
        if len(text) < 2 or any(h in low for h in self.halluc):
            return None
        return text
