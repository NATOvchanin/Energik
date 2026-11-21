"""Мозг Энергика: языковая модель в Ollama, потоковый ответ по предложениям."""
import random
import re
import time

import ollama

import tools
from config import ROOT

SENT_END = re.compile(r'[.!?…]+["»)]*\s')
SOFT_BREAKS = (", ", " — ", "; ", ": ")
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
FILLERS = ["Сек, гляну в сети.", "Ща проверю.", "Погоди, полезу в интернет.", "Так, ищу."]
CJK_RE = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+")  # иероглифы-глюки модели


def split_stream(chunks, soft_limit: int = 110, first_limit: int = 55,
                 truncated=lambda: False):
    """
    Превращает поток кусочков текста в поток предложений.
    Слишком длинное предложение режется по запятой, чтобы озвучка стартовала быстрее.
    Если ответ оборвался по лимиту токенов, недописанный хвост выбрасывается.
    """
    buf = ""
    emitted = 0
    for ch in chunks:
        buf += ch
        while True:
            m = SENT_END.search(buf)
            if m:
                sent, buf = buf[:m.end()].strip(), buf[m.end():]
                if sent:
                    emitted += 1
                    yield sent
                continue
            limit = first_limit if emitted == 0 else soft_limit
            if len(buf) > limit:
                cut = max(buf.rfind(b, 0, limit) for b in SOFT_BREAKS)
                if cut > 20:
                    emitted += 1
                    yield buf[:cut + 1].strip()
                    buf = buf[cut + 1:]
                    continue
            break
    tail = buf.strip()
    if tail and (not truncated() or tail[-1] in '.!?…"»)'):
        yield tail


def _field(obj, name):
    try:
        return obj[name]
    except Exception:
        return getattr(obj, name, None)


class Brain:
    def __init__(self, cfg: dict):
        b = cfg["brain"]
        self.model = b["model"]
        self.opts = {"num_ctx": int(b["num_ctx"]),
                     "temperature": float(b["temperature"]),
                     "num_predict": int(b["max_tokens"])}
        self.keep_alive = b["keep_alive"]
        self.max_turns = int(b["history_turns"])
        self.max_sentences = int(b.get("max_sentences", 3))
        self.search_on = bool(b.get("search", True))
        self.search_timeout = float(b.get("search_timeout", 7))
        self.search_backend = b.get("search_backend", "auto")
        self.last_search = None  # (номер реплики, запрос, найденное)
        self.persona = (ROOT / b["persona_file"]).read_text(encoding="utf-8").strip()
        self.history: list[dict] = []
        self._truncated = False
        self.t_start = time.time()
        self.turns = 0
        self._check_model()

    def _check_model(self) -> None:
        try:
            models = ollama.list()["models"]
            names = [(m.get("model") or m.get("name") or "") for m in models]
        except Exception as e:
            raise SystemExit(f"[МОЗГ] Ollama не отвечает ({e}).\n"
                             "Запусти программу Ollama (значок в трее) и повтори.")
        if not any(n == self.model or n.startswith(self.model) for n in names):
            raise SystemExit(f"[МОЗГ] Модель '{self.model}' не скачана.\n"
                             f"Выполни:  ollama pull {self.model}\n"
                             f"Скачано сейчас: {', '.join(names) or 'ничего'}")

    def warmup(self) -> None:
        print(f"[МОЗГ] Загружаю {self.model} в видеопамять...")
        t = time.time()
        for _ in self._chunks([{"role": "user", "content": "Скажи одно слово: готов."}],
                              max_tokens=5):
            pass
        print(f"[МОЗГ] Готов за {time.time() - t:.1f} с")

    def reset(self) -> None:
        self.history.clear()
        self.t_start = time.time()
        self.turns = 0

    def _system(self) -> str:
        minutes = int((time.time() - self.t_start) / 60)
        dur = "меньше минуты" if minutes < 1 else f"{minutes} мин."
        now = time.localtime()
        caps = ("Что у тебя реально есть: слух (микрофон), голос и память на текущий сеанс. "
                "Экран ты пока не видишь. ")
        if self.search_on:
            caps += ("Поиск в интернете запускается, когда Босс говорит «загугли ...», "
                     "«найди в интернете ...», «кто такой ...» или просит новости. Результаты приходят "
                     "в блоке «Результаты поиска». Если такого блока нет, значит ты ничего не искал "
                     "и не открывал: не говори, что смотрел страницы или нашёл что-то, а подскажи "
                     "Боссу сказать «загугли». ")
        else:
            caps += "Доступа в интернет у тебя сейчас нет. "
        recent = ""
        if self.last_search and 0 < self.turns - self.last_search[0] <= 4:
            _, q, found = self.last_search
            recent = (f"\nНедавний поиск (запрос «{q}»), если Босс спросит про него, опирайся на это, "
                      f"числа и названия бери дословно, источники называй только из квадратных скобок:\n"
                      f"{found[:700]}")
        return (self.persona + "\n\n"
                f"Сегодня: {time.strftime('%d.%m.%Y', now)}, {WEEKDAYS[now.tm_wday]}, "
                f"время {time.strftime('%H:%M', now)}.\n"
                f"Факты о сеансе: вы с Боссом разговариваете {dur}, реплик Босса: {self.turns}. "
                "Других цифр о времени и истории не придумывай.\n" + caps +
                "Не заявляй о возможностях, которых нет в этом списке." + recent)

    def _chunks(self, messages, max_tokens=None):
        opts = dict(self.opts)
        if max_tokens:
            opts["num_predict"] = max_tokens
        kw = dict(model=self.model, messages=messages, stream=True,
                  options=opts, keep_alive=self.keep_alive)
        self._truncated = False
        for try_think_off in (True, False):
            args = dict(kw, think=False) if try_think_off else kw
            started = False
            try:
                for part in ollama.chat(**args):
                    msg = _field(part, "message")
                    piece = _field(msg, "content") if msg is not None else ""
                    if piece:
                        started = True
                        yield piece
                    if _field(part, "done_reason") == "length":
                        self._truncated = True
                return
            except (TypeError, ollama.ResponseError) as e:
                if try_think_off and not started and ("think" in str(e).lower()
                                                      or isinstance(e, TypeError)):
                    continue
                raise

    def reply(self, user_text: str):
        """Генератор: выдаёт ответ по одному предложению, не больше max_sentences."""
        self.turns += 1
        self.history.append({"role": "user", "content": user_text})

        note = ""
        req = tools.parse_search_request(user_text) if self.search_on else None
        if req:
            kind, query = req
            yield random.choice(FILLERS)  # пока ищем, не молчим (в историю не попадает)
            print(f"[ИНТЕРНЕТ] ищу ({'новости' if kind == 'news' else 'текст'}): {query}")
            t = time.time()
            found, err = tools.web_search(query, kind=kind, timeout=self.search_timeout,
                                          backend=self.search_backend)
            if found:
                print(f"[ИНТЕРНЕТ] нашёл за {time.time() - t:.1f} с")
                self.last_search = (self.turns, query, found)
                note = (f"\n\n[Результаты поиска в интернете по запросу «{query}»:\n{found}\n"
                        "Правила: опирайся только на эти результаты, числа и названия бери дословно, "
                        "источники называй только из квадратных скобок. Если нужного в выдержках нет, "
                        "так и скажи. Первое предложение по делу, второе короткий подкол.]")
            else:
                print(f"[ИНТЕРНЕТ] не вышло за {time.time() - t:.1f} с: {err}")
                note = (f"\n\n[Поиск в интернете не удался ({err}). Скажи Боссу об этом коротко "
                        "и с издёвкой, ничего не выдумывай.]")

        hist = self.history[-self.max_turns * 2:]
        messages = ([{"role": "system", "content": self._system()}] + hist[:-1] +
                    [{"role": "user", "content": user_text + note}])
        spoken: list[str] = []
        real = 0  # считаем только законченные предложения, а не куски, нарезанные по запятой
        stream = split_stream(self._chunks(messages), truncated=lambda: self._truncated)
        try:
            for sentence in stream:
                sentence = CJK_RE.sub("", sentence).strip()
                if not sentence:
                    continue
                spoken.append(sentence)
                yield sentence
                if sentence[-1] in '.!?…"»)':
                    real += 1
                if real >= self.max_sentences:
                    break  # остальное модель договорит в пустоту, мы это обрываем
        finally:
            stream.close()
            answer = " ".join(spoken).strip()
            if answer:  # в историю идёт только то, что реально прозвучало
                self.history.append({"role": "assistant", "content": answer})
            self.history[:] = self.history[-self.max_turns * 2:]
