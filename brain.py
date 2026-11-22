"""
Мозг Энергика: диалог + любопытство (сам идёт в сеть) + долговременная память.
Языковая модель получает уже подготовленный контекст; поиск, память и записи делает программа.
"""
import random
import re
import threading
import time

import curiosity
import memory as memlib
import tools
from config import ROOT

SENT_END = re.compile(r'[.!?…]+["»)]*\s')
SOFT_BREAKS = (", ", " — ", "; ", ": ")
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
FILLERS = ["Сек, гляну.", "Ща проверю.", "Погоди, выясню.", "Так, дай-ка загляну в сеть."]
CJK_RE = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+")  # иероглифы-глюки модели


def split_stream(chunks, soft_limit: int = 110, first_limit: int = 55, truncated=lambda: False):
    """Поток кусочков текста -> поток предложений. Длинное режется по запятой, обрыв по лимиту выбрасывается."""
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


class Brain:
    def __init__(self, cfg: dict, dialogue, utility, mem: memlib.Memory):
        b = cfg["brain"]
        self.cfg = cfg
        self.llm, self.util, self.mem = dialogue, utility, mem
        self.temperature = float(b.get("temperature", 0.8))
        self.max_tokens = int(b["max_tokens"])
        self.num_ctx = int(b["num_ctx"])
        self.max_turns = int(b["history_turns"])
        self.max_sentences = int(b.get("max_sentences", 3))
        self.search_on = bool(b.get("search", True))
        self.curious = bool(b.get("curiosity", True)) and self.search_on
        self.memory_every = int(b.get("memory_every_turns", 8))
        self.news_feeds = b.get("news_feeds", ["https://lenta.ru/rss/news", "https://www.interfax.ru/rss.asp",
                                               "https://tass.ru/rss/v2.xml"])
        self.persona = (ROOT / b["persona_file"]).read_text(encoding="utf-8").strip()
        self.history: list[dict] = []
        self.transcript: list[str] = []      # строки для дневника памяти
        self._digested = 0                   # сколько строк transcript уже ушло в память
        self._digest_thread: threading.Thread | None = None
        self.last_search = None              # (номер реплики, запрос, найденное)
        self.summary = ""                    # краткое содержание начала длинного разговора
        self._compressing = False
        self._fit_used = 0                   # сколько реплик истории влезло в последний запрос
        self._core_turn = -99                # когда последний раз показывали «главное о Боссе»
        self.news = None
        self.t_start = time.time()
        self.turns = 0

    # ---------- подготовка контекста ----------
    def reset(self) -> None:
        """«Забудь всё»: сбрасывает только текущий разговор. Долговременная память остаётся."""
        self.history.clear()
        self.summary = ""
        self.news = None
        self.last_search = None
        self.t_start = time.time()
        self.turns = 0

    def _recall(self, user_text: str) -> str:
        """Выбирает воспоминания. Главное о Боссе показываем раз в несколько реплик, остальное только
        если оно правда подходит к теме (и не то, что только что вспоминали)."""
        query = user_text
        if len(re.findall(r"\w+", user_text)) < 5:       # короткая реплика: добавим смысл прошлой
            prev = [m["content"] for m in self.history[:-1] if m["role"] == "user"]
            if prev:
                query += " " + prev[-1]
        core = []
        if self.turns == 1 or self.turns - self._core_turn >= 6:
            core = list(self.mem.core(3))
            self._core_turn = self.turns
        found = self.mem.search(query, limit=2, exclude={r["id"] for r in core})
        rows = core + list(found)
        lines = [f"- ({memlib.ago(r['ts'])}) {r['text']}" for r in rows]
        if self.turns <= 2:
            last = self.mem.last_session()
            if last:
                lines.append(f"- (прошлый разговор, {memlib.ago(last['ts'])}) {last['text']}")
        if rows:
            self.mem.touch([r["id"] for r in rows])
            print("[ПАМЯТЬ] вспомнил: " + " | ".join(r["text"][:50] for r in rows))
        if not lines:
            return ""
        return ("\nЧто ты помнишь о Боссе и ваших разговорах. Упоминай это редко, не чаще раза в пять реплик "
                "и только если правда к теме, одной короткой отсылкой. Не пересказывай списком, не "
                "добавляй деталей, которых здесь нет:\n" + "\n".join(lines))

    def _system(self, user_text: str = "") -> str:
        minutes = int((time.time() - self.t_start) / 60)
        dur = "меньше минуты" if minutes < 1 else f"{minutes} мин."
        now = time.localtime()
        caps = ("Что у тебя реально есть: слух (микрофон), голос, память и умение сходить в интернет, "
                "когда это нужно. Экран ты пока не видишь. Если для ответа нужно было что-то выяснить, "
                "это делается до твоего ответа и приходит блоком «Результаты поиска». Если такого блока "
                "нет, значит ты ничего не искал и не открывал: не говори, что смотрел страницы, и "
                "не выдумывай факты, а честно скажи, что не знаешь. ")
        recent = ""
        if self.last_search and 0 < self.turns - self.last_search[0] <= 4:
            _, q, found = self.last_search
            recent = (f"\nНедавний поиск (запрос «{q}»), если Босс спросит про него, опирайся на это, "
                      f"числа и названия бери дословно, источники называй только из квадратных скобок:\n"
                      f"{found[:700]}")
        earlier = (f"\nКратко о начале этого разговора (уже не в истории): {self.summary}"
                   if self.summary else "")
        return (self.persona + "\n\n"
                f"Сегодня: {time.strftime('%d.%m.%Y', now)}, {WEEKDAYS[now.tm_wday]}, "
                f"время {time.strftime('%H:%M', now)}.\n"
                f"Факты о сеансе: вы с Боссом разговариваете {dur}, реплик Босса: {self.turns}. "
                "Других цифр о времени и истории не придумывай.\n" + caps + recent + earlier +
                self._recall(user_text))

    # ---------- любопытство ----------
    def _intent(self, user_text: str):
        """Что нужно выяснить перед ответом: (вид, запрос, откуда решение) или None."""
        if not self.search_on:
            return None
        if self.news and self.turns - self.news["turn"] <= 3 and tools.is_more_request(user_text):
            return "news", "новости", "продолжение"
        req = tools.parse_search_request(user_text)
        if req:
            return req[0], req[1], "прямая просьба"
        if self.curious:
            t = time.time()
            d = curiosity.decide(self.util, user_text, self.history[:-1])
            if d:
                print(f"[ЛЮБОПЫТСТВО] сам решил выяснить ({time.time() - t:.1f} с): {d[0]} «{d[1]}»")
                return d[0], d[1], "само"
        return None

    def _news_cached(self, query: str) -> bool:
        n = self.news
        return bool(query == "новости" and n and time.time() - n["ts"] <= 600 and n["pos"] < len(n["items"]))

    def _next_news(self):
        n = self.news
        if not self._news_cached("новости"):
            items, err = tools.fetch_news(self.news_feeds)
            if not items:
                return None, err
            n = self.news = {"items": items, "pos": 0, "ts": time.time(), "turn": self.turns}
        it = n["items"][n["pos"]]
        n["pos"] += 1
        n["turn"] = self.turns
        date = f" ({it['date']})" if it["date"] else ""
        return f"[{it['source']}]{date} {it['title']}. {it['summary']}"[:700], None

    def _lookup(self, kind: str, query: str, question: str):
        if kind == "news" and query == "новости":
            found, err = self._next_news()
            if found:
                return found, None
            print(f"[ИНТЕРНЕТ] ленты недоступны ({err}), пробую поисковик")
            kind = "search"
        return curiosity.research(self.util, "who" if kind == "who" else "search", query,
                                  question, self.cfg)

    # ---------- ответ ----------
    def reply(self, user_text: str):
        """Генератор: выдаёт ответ по одному предложению, не больше max_sentences."""
        self.turns += 1
        self.history.append({"role": "user", "content": user_text})

        note = ""
        intent = self._intent(user_text)
        if intent:
            kind, query, why = intent
            if not (kind == "news" and self._news_cached(query)):
                yield random.choice(FILLERS)  # пока выясняем, не молчим (в историю не попадает)
            print(f"[ИНТЕРНЕТ] ({why}) {kind}: {query}")
            t = time.time()
            found, err = self._lookup(kind, query, user_text)
            if found:
                print(f"[ИНТЕРНЕТ] готово за {time.time() - t:.1f} с")
                self.last_search = (self.turns, "новость" if query == "новости" else query, found)
                if kind == "news" and query == "новости":
                    note = (f"\n\n[Одна свежая новость: {found}\nПерескажи её своими словами в одном "
                            "коротком предложении, ничего не добавляя от себя, затем короткий подкол. "
                            "Если называешь источник, то по-человечески и без адреса (не «lenta.ru», а «Лента»).]")
                else:
                    note = (f"\n\n[Результаты поиска по запросу «{query}»:\n{found}\n"
                            "Правила: опирайся только на эти результаты, числа и названия бери дословно, "
                            "источник, если называешь, то по-человечески и без адреса (не «wciom.ru», а «ВЦИОМ»). "
                            "Если нужного в них нет, так и скажи. Первое предложение по делу, второе короткий подкол.]")
            else:
                print(f"[ИНТЕРНЕТ] не вышло за {time.time() - t:.1f} с: {err}")
                note = (f"\n\n[Выяснить не удалось ({err}). Скажи Боссу об этом коротко и с издёвкой, "
                        "ничего не выдумывай.]")

        system = self._system(user_text)
        user_msg = user_text + note
        # Историю подбираем под размер контекста модели: свежие реплики целиком, пока есть место
        # (иначе Ollama молча выбросила бы начало запроса). Оценка: ~2.2 символа на токен.
        budget = self.num_ctx - self.max_tokens - 150 - self._tok(system) - self._tok(user_msg)
        hist: list[dict] = []
        for m in reversed(self.history[:-1][-(self.max_turns * 2):]):
            cost = self._tok(m["content"]) + 6
            if cost > budget:
                break
            budget -= cost
            hist.insert(0, m)
        self._fit_used = len(hist) + 1
        messages = ([{"role": "system", "content": system}] + hist +
                    [{"role": "user", "content": user_msg}])
        spoken: list[str] = []
        real = 0
        stream = split_stream(self.llm.stream(messages, self.temperature, self.max_tokens),
                              truncated=lambda: self.llm.truncated)
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
                    break
        finally:
            stream.close()
            answer = " ".join(spoken).strip()
            if answer:  # в историю идёт только то, что реально прозвучало
                self.history.append({"role": "assistant", "content": answer})
            self._maybe_compress()
            self.transcript.append(f"Босс: {user_text}")
            self.transcript.append(f"Энергик: {answer or '(промолчал)'}")
            self._maybe_digest()

    @staticmethod
    def _tok(text: str) -> int:
        return int(len(text) / 2.2) + 4

    def _maybe_compress(self) -> None:
        """Когда история разрастается, самые старые реплики превращаются в краткое содержание
        (в фоне). Так Энергик помнит ход длинного разговора, а контекст модели не раздувается."""
        keep = self.max_turns * 2
        unfit = (len(self.history) - 1) - self._fit_used     # старые реплики, не попавшие в запрос
        if len(self.history) <= keep + 8 and unfit < 6:
            return
        if len(self.history) > keep + 16:      # сжатие не удалось: просто обрезаем, чтобы не копилось
            self.history[:] = self.history[-(keep + 8):]
            return
        if self._compressing:
            return
        old = list(self.history[:8])
        self._compressing = True

        def work():
            try:
                convo = "\n".join(("Босс: " if m["role"] == "user" else "Энергик: ") + m["content"][:300]
                                   for m in old)
                prompt = ("Обнови краткое содержание разговора Босса и Энергика. Было: "
                          f"«{self.summary or 'пока ничего'}». Новые реплики:\n{convo}\n\n"
                          "Напиши 3-4 коротких предложения: о чём говорили, о чём договорились, какие вопросы "
                          "остались открытыми. Только то, что реально сказано, без выдумок.")
                text = self.util.complete([{"role": "user", "content": prompt}],
                                          temperature=0.2, max_tokens=200)
                if text and len(text) > 20:
                    self.summary = text[:700]
                    if self.history[:8] == old:
                        del self.history[:8]
            except Exception as e:
                print(f"[МОЗГ] не удалось сжать историю: {e}")
            finally:
                self._compressing = False

        threading.Thread(target=work, daemon=True).start()

    # ---------- приветствие и память ----------
    def greeting(self):
        """Приветствие с отсылкой к прошлому разговору. None, если Энергик пока ничего не помнит."""
        if self.mem.count() == 0:
            return None
        sys_prompt = self.persona + self._recall("") + "\n\nФормат: одно-два коротких предложения вслух по-русски."
        try:
            text = self.llm.complete(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": "[Босс только что включил программу. Поздоровайся с ним. "
                                             "Если уместно, коротко отсылайся к прошлому разговору "
                                             "или к тому, что ты о нём помнишь.]"}],
                temperature=0.9, max_tokens=80)
        except Exception as e:
            print(f"[МОЗГ] приветствие не вышло: {e}")
            return None
        return CJK_RE.sub("", text).strip() or None

    def _maybe_digest(self) -> None:
        pending = len(self.transcript) - self._digested
        if pending < self.memory_every * 2:
            return
        if self._digest_thread and self._digest_thread.is_alive():
            return
        lines = self.transcript[self._digested:]
        self._digested = len(self.transcript)
        self._digest_thread = threading.Thread(
            target=memlib.digest, args=(self.util, self.mem, lines), daemon=True)
        self._digest_thread.start()

    def close(self) -> None:
        """Перед выходом: дописывает дневник и итог разговора."""
        if self._digest_thread and self._digest_thread.is_alive():
            self._digest_thread.join(timeout=30)
        lines = self.transcript[self._digested:]
        if self.turns >= 2:
            print("[ПАМЯТЬ] записываю итоги разговора...")
            summary_source = lines if len(lines) >= 4 else self.transcript[-12:]
            memlib.digest(self.util, self.mem, summary_source, with_summary=True)
            self._digested = len(self.transcript)
