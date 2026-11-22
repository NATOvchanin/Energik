"""
Долговременная память Энергика: SQLite + полнотекстовый поиск.
Файл базы: data/energik.db (его можно смотреть и править через memory_tool.py).

Виды заметок: fact (факт о Боссе), preference (что нравится/не нравится), moment (запомнившийся
эпизод), joke (общая шутка), promise (обещание/планы), session (итог разговора).
"""
import json
import math
import re
import sqlite3
import threading
import time

from config import ROOT

DB_PATH = ROOT / "data" / "energik.db"
KINDS = {"fact", "preference", "moment", "joke", "promise", "session"}


def _stem(w: str) -> str:
    return w[:5] if len(w) > 5 else w[:-1]


# Слова, по которым нельзя искать воспоминания: они есть в любой болтовне («интересно», «расскажи»...)
_STOP = {_stem(w) for w in """
интересно расскажи рассказать сказать скажи хорошо просто можно нужно очень тогда когда потом который
сейчас теперь вообще кажется такой прямо давай знаешь думаю думаешь говорил говорит говорить делать
сделать смотри слушай типа блин короче значит например вроде чтобы потому почему зачем какой какая
какие этого этот этой свой своего себя тебе меня тебя разговор разговора сегодня вчера босс энергик
""".split()}


def stems(text: str) -> list[str]:
    """Грубая «основа» слова: хватает, чтобы «книга» находила «книги» и «книгу»."""
    out = []
    for w in re.findall(r"\w+", text.lower()):
        if len(w) < 4:
            continue
        s = _stem(w)
        if s not in _STOP:
            out.append(s)
    return out


class Memory:
    def __init__(self, path=DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS notes(
                id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT, importance INTEGER,
                tags TEXT DEFAULT '', last_used REAL DEFAULT 0, use_count INTEGER DEFAULT 0)""")
            try:
                self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                                "USING fts5(text, tags, tokenize='unicode61 remove_diacritics 2')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # в этой сборке Python нет FTS5, тогда ищем медленнее, но работает
            self.db.commit()

    # ---------- запись ----------
    def add(self, kind: str, text: str, importance: int = 3, tags: str = "") -> int | None:
        kind = kind if kind in KINDS else "moment"
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 8:
            return None
        importance = max(1, min(5, int(importance)))
        new = set(stems(text))
        with self.lock:
            for row in self.db.execute("SELECT id, text, importance FROM notes WHERE kind = ?", (kind,)).fetchall():
                old = set(stems(row["text"]))
                small = min(len(new), len(old))
                if small >= 2 and len(new & old) / small >= 0.8:      # по сути то же самое
                    longer = text if len(text) > len(row["text"]) else row["text"]
                    self.db.execute("UPDATE notes SET ts = ?, importance = ?, text = ? WHERE id = ?",
                                    (time.time(), max(importance, row["importance"]), longer, row["id"]))
                    if self.fts:
                        self.db.execute("DELETE FROM notes_fts WHERE rowid = ?", (row["id"],))
                        self.db.execute("INSERT INTO notes_fts(rowid, text, tags) VALUES(?,?,?)",
                                        (row["id"], longer, tags))
                    self.db.commit()
                    return row["id"]
            cur = self.db.execute("INSERT INTO notes(ts, kind, text, importance, tags) VALUES(?,?,?,?,?)",
                                  (time.time(), kind, text, importance, tags))
            nid = cur.lastrowid
            if self.fts:
                self.db.execute("INSERT INTO notes_fts(rowid, text, tags) VALUES(?,?,?)", (nid, text, tags))
            self.db.commit()
            return nid

    def forget(self, note_id: int) -> bool:
        with self.lock:
            cur = self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            if self.fts:
                self.db.execute("DELETE FROM notes_fts WHERE rowid = ?", (note_id,))
            self.db.commit()
            return cur.rowcount > 0

    def touch(self, ids) -> None:
        with self.lock:
            now = time.time()
            for i in ids:
                self.db.execute("UPDATE notes SET last_used = ?, use_count = use_count + 1 WHERE id = ?", (now, i))
            self.db.commit()

    # ---------- чтение ----------
    def all(self, limit: int = 500):
        with self.lock:
            return list(self.db.execute("SELECT * FROM notes ORDER BY ts DESC LIMIT ?", (limit,)))

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def core(self, limit: int = 3):
        """Самое важное о Боссе: показывается всегда, независимо от темы."""
        with self.lock:
            return list(self.db.execute(
                "SELECT * FROM notes WHERE kind IN ('fact','preference') AND importance >= 4 "
                "ORDER BY importance DESC, ts DESC LIMIT ?", (limit,)))

    def last_session(self):
        with self.lock:
            return self.db.execute("SELECT * FROM notes WHERE kind = 'session' ORDER BY ts DESC LIMIT 1").fetchone()

    def search(self, text: str, limit: int = 3, exclude=(), cooldown_s: int = 900):
        """
        Находит заметки по теме. Берёт только действительно подходящие (минимум два общих слова, либо
        одно, но у важной заметки) и не повторяет то, что вспоминали последние 15 минут.
        """
        st = stems(text)
        if not st:
            return []
        qset = set(st)
        now = time.time()
        with self.lock:
            rows = []
            if self.fts:
                q = " OR ".join(f'"{s}"*' for s in dict.fromkeys(st))
                try:
                    rows = list(self.db.execute(
                        "SELECT n.*, bm25(notes_fts) AS rank FROM notes_fts "
                        "JOIN notes n ON n.id = notes_fts.rowid WHERE notes_fts MATCH ? "
                        "ORDER BY rank LIMIT 20", (q,)))
                except sqlite3.OperationalError:
                    rows = []
            else:
                for r in self.db.execute("SELECT * FROM notes ORDER BY ts DESC LIMIT 400"):
                    hits = len(qset & set(stems(r["text"])))
                    if hits:
                        d = dict(r)
                        d["rank"] = -float(hits)
                        rows.append(d)
        scored = []
        for r in rows:
            if r["id"] in exclude or r["kind"] == "session":
                continue
            if now - r["last_used"] < cooldown_s:
                continue                                     # только что вспоминали: не повторяемся
            overlap = len(qset & set(stems(r["text"])))
            if overlap < 2 and not (overlap >= 1 and r["importance"] >= 4):
                continue                                     # слишком слабое совпадение
            age_days = (now - r["ts"]) / 86400
            score = -r["rank"] + 0.4 * r["importance"] + 1.0 * math.exp(-age_days / 30) + overlap
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    def prune(self, max_notes: int = 1500) -> int:
        """Уборка: выбрасывает слабое и старое, чтобы память не превращалась в свалку."""
        now = time.time()
        with self.lock:
            n0 = self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            # слабые заметки, которые за две недели ни разу не пригодились
            self.db.execute("DELETE FROM notes WHERE importance <= 2 AND use_count = 0 AND ts < ?",
                            (now - 14 * 86400,))
            # итогов разговоров достаточно последних двадцати
            self.db.execute("DELETE FROM notes WHERE kind = 'session' AND id NOT IN "
                            "(SELECT id FROM notes WHERE kind = 'session' ORDER BY ts DESC LIMIT 20)")
            # общий потолок: убираем самые слабые и старые
            total = self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            if total > max_notes:
                self.db.execute("DELETE FROM notes WHERE id IN (SELECT id FROM notes "
                                "ORDER BY importance ASC, use_count ASC, ts ASC LIMIT ?)", (total - max_notes,))
            if self.fts:
                self.db.execute("DELETE FROM notes_fts WHERE rowid NOT IN (SELECT id FROM notes)")
            self.db.commit()
            return n0 - self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]


def ago(ts: float) -> str:
    d = (time.time() - ts) / 86400
    if d < 0.04:
        return "только что"
    if d < 1:
        return "сегодня"
    if d < 2:
        return "вчера"
    if d < 7:
        return f"{int(d)} дн. назад"
    if d < 30:
        return f"{int(d // 7)} нед. назад"
    return f"{int(d // 30)} мес. назад"


# ---------- запись воспоминаний моделью ----------
DIGEST_PROMPT = """Ты ведёшь дневник Мистера Энергика. Ниже фрагмент разговора с Боссом. Выпиши только то, что стоит запомнить надолго.

Правила:
- Факты о Боссе (его дела, проекты, вкусы, привычки, планы) бери ТОЛЬКО из реплик с пометкой «Босс:». Если что-то о Боссе сказал Энергик, а сам Босс этого не говорил, это не факт: Энергик часто выдумывает.
- Запоминай: устойчивые факты и предпочтения, запомнившиеся эпизоды и общие шутки, обещания и планы.
- Не запоминай: болтовню, оскорбления, мат, проверки программы, ошибки поиска, мимолётные реплики, вопросы Босса к Энергику.
- Не больше 3 заметок. Если запоминать нечего, верни пустой список.
- Каждая заметка: одно короткое законченное предложение, от третьего лица («Босс ...», «Энергик ...»).
- evidence: короткая ДОСЛОВНАЯ цитата из реплики Босса, на которой держится заметка (обязательна для fact, preference и promise).
- importance от 1 до 5 (5 = очень важно о Боссе).
- summary: одно предложение о чём был разговор.

Ответ строго в JSON, без пояснений:
{"notes":[{"kind":"fact|preference|moment|joke|promise","text":"...","evidence":"...","importance":3}],"summary":"..."}

Разговор:
"""


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"\w+", s.lower()) if len(w) > 2]


def supported(evidence: str, pool_text: str, thr: float = 0.75) -> bool:
    """Цитата должна реально присутствовать в разговоре (проверка слово в слово, без морфологии)."""
    ws = _words(evidence)
    if len(ws) < 2:
        return False
    pool = set(_words(pool_text))
    return sum(w in pool for w in ws) / len(ws) >= thr


def parse_json_object(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def digest(llm, memory: Memory, lines: list[str], with_summary: bool = False) -> int:
    """Просит модель выбрать заметки из разговора и сохраняет их. Возвращает число сохранённых."""
    if len(lines) < 2:
        return 0
    convo = "\n".join(lines)[-2500:]
    try:
        raw = llm.complete([{"role": "user", "content": DIGEST_PROMPT + convo}],
                           temperature=0.1, max_tokens=350)
    except Exception as e:
        print(f"[ПАМЯТЬ] не удалось записать: {e}")
        return 0
    data = parse_json_object(raw)
    if not isinstance(data, dict):
        print("[ПАМЯТЬ] модель вернула нечитаемый ответ, пропускаю")
        return 0
    boss_text = " ".join(l.split(":", 1)[1] for l in lines if l.startswith("Босс:"))
    all_text = " ".join(l.split(":", 1)[1] for l in lines if ":" in l)
    saved = 0
    for n in (data.get("notes") or [])[:3]:
        if not (isinstance(n, dict) and isinstance(n.get("text"), str)):
            continue
        kind = str(n.get("kind", "moment"))
        try:
            imp = int(n.get("importance", 3))
        except (TypeError, ValueError):
            imp = 3
        evidence = str(n.get("evidence", ""))
        # всё, что сказано про Босса, должно держаться на его собственных словах (а не на выдумках Энергика)
        strict = kind in ("fact", "preference", "promise") or n["text"].lstrip().startswith("Босс")
        ok = supported(evidence, boss_text) if strict else (not evidence or supported(evidence, all_text))
        if not ok:
            print(f"[ПАМЯТЬ] отбросил (нет подтверждения словами Босса): {n['text']}")
            continue
        if imp >= 2 and memory.add(kind, n["text"], imp if evidence else min(imp, 3)):
            saved += 1
            print(f"[ПАМЯТЬ] запомнил ({kind}, {imp}): {n['text']}")
    if with_summary and isinstance(data.get("summary"), str) and len(data["summary"]) > 10:
        memory.add("session", data["summary"], 3)
    return saved
