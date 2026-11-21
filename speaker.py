"""
Конвейер озвучки: предложения -> синтез и эффекты -> воспроизведение.
Пока играет первое предложение, второе уже готовится, поэтому длинные ответы не тормозят.
"""
import queue
import re
import threading
import time


def sanitize(text: str) -> str:
    """Убираем смайлики, звёздочки и прочий мусор, который нельзя произнести."""
    t = re.sub(r"[^А-Яа-яЁёA-Za-z0-9\s.,!?…:;\-—()«»\"'%]", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return re.sub(r"\s+([,.!?…:;])", r"\1", t)


class Speaker:
    def __init__(self, voice):
        self.voice = voice
        self.text_q: queue.Queue = queue.Queue()
        self.audio_q: queue.Queue = queue.Queue(maxsize=2)
        self.cv = threading.Condition()
        self.pending = 0
        self.first_play_ts: float | None = None
        threading.Thread(target=self._render_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def start_utterance(self) -> None:
        self.first_play_ts = None

    def say(self, text: str) -> None:
        text = sanitize(text)
        print(f"Энергик: {text}")
        if not re.search(r"[А-Яа-яЁё]", text):
            print("   [ГОЛОС] нет русских букв, пропускаю озвучку")
            return
        with self.cv:
            self.pending += 1
        self.text_q.put(text)

    def wait_done(self) -> None:
        with self.cv:
            self.cv.wait_for(lambda: self.pending == 0)
        if hasattr(self.voice, "drain"):
            self.voice.drain()

    def _render_loop(self) -> None:
        while True:
            text = self.text_q.get()
            try:
                audio = self.voice.render(text)
            except Exception as e:
                print(f"[ГОЛОС] ошибка синтеза: {e}")
                audio = None
            self.audio_q.put(audio)

    def _play_loop(self) -> None:
        while True:
            audio = self.audio_q.get()
            try:
                if audio is not None and len(audio):
                    if self.first_play_ts is None:
                        self.first_play_ts = time.time()
                    self.voice.play(audio)
            except Exception as e:
                print(f"[ГОЛОС] ошибка воспроизведения: {e}")
            finally:
                with self.cv:
                    self.pending -= 1
                    self.cv.notify_all()
