"""Инструменты Энергика: поиск в интернете (необязательный, без сети он просто не работает)."""
import re
import threading
import time
from urllib.parse import urlparse

# --- как понять, что Босс просит что-то найти ---
_VERBS = r"(?:за|по)?гугл\w*|поищ\w*|найд\w*|найти|откро\w*|открыть|посмотр\w*|глян\w*"
_WHERE = r"в\s+(?:интернет\w*|сети|гугле|яндексе)"
GOOGLE_RE = re.compile(r"\b(?:за|по)?гугл\w*", re.I)
VERB_RE = re.compile(r"\b(?:" + _VERBS + r")", re.I)
WHERE_RE = re.compile(_WHERE, re.I)
WHO_RE = re.compile(r"\bкто\s+так(?:ой|ая|ие|ое)\b", re.I)
NEWS_ASK_RE = re.compile(r"\b(?:что\s+нового|какие\s+новости|последние\s+новости|что\s+пишут)", re.I)
NEWS_RE = re.compile(r"новост\w*", re.I)
LEAD_RE = re.compile(r"^(?:мне|пожалуйста|ка|ну|там|что-нибудь|что-то|про|о|об)\s+", re.I)
STOP_WORD_RE = re.compile(r"^(?:и|а|но|или|да)$|^(?:" + _VERBS + r")$", re.I)


def parse_search_request(text: str):
    """Возвращает (вид, запрос), где вид 'text' или 'news'. None, если искать не просили."""
    if NEWS_ASK_RE.search(text):
        return "news", "новости"
    m_g, m_who, m_v = GOOGLE_RE.search(text), WHO_RE.search(text), VERB_RE.search(text)
    if m_g:
        start = m_g.end()
    elif m_who:
        start = m_who.end()
    elif m_v and (WHERE_RE.search(text) or NEWS_RE.search(text)):
        start = m_v.end()
    else:
        return None

    rest = re.split(r"[.!?…]", text[start:])[0]          # только первое предложение после команды
    rest = WHERE_RE.sub(" ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" ,;:-—")
    prev = None
    while prev != rest:                                    # снимаем «мне», «пожалуйста» в начале
        prev, rest = rest, LEAD_RE.sub("", rest).strip()

    nm = NEWS_RE.search(rest)
    if nm:                                                 # новости: слово «новости» + пара уточнений
        words = []
        for w in rest[nm.start():].split()[1:5]:
            if STOP_WORD_RE.match(w.strip(",.;:")):
                break
            words.append(w.strip(",.;:"))
        return "news", " ".join(["новости"] + words)

    words = rest.split()[:10]
    query = " ".join(w.strip(",;:") for w in words).strip()
    return ("text", query) if len(query) >= 3 else None


# --- сам поиск ---
def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _search(query: str, kind: str, max_results: int, backend: str):
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    d = DDGS(timeout=6)
    fn = d.news if kind == "news" else d.text
    kw = dict(region="ru-ru", max_results=max_results)
    try:
        return list(fn(query, backend=backend, **kw))
    except TypeError:  # старая версия библиотеки без выбора движка
        return list(fn(query, **kw))


def web_search(query: str, kind: str = "text", max_results: int = 4, max_chars: int = 900,
               timeout: float = 7.0, backend: str = "auto"):
    """Возвращает (текст, None) при успехе или (None, причина) при неудаче."""
    query = query.strip()
    if len(query) < 3:
        return None, "запрос слишком короткий"
    box: dict = {}

    def work():
        try:
            box["items"] = _search(query, kind, max_results, backend)
        except ImportError:
            box["err"] = "не установлена библиотека ddgs (pip install ddgs)"
        except Exception as e:
            box["err"] = str(e)[:120] or type(e).__name__

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        return None, f"поиск не ответил за {timeout:.0f} с"
    if "err" in box:
        return None, box["err"]
    items = box.get("items") or []
    if not items:
        return None, "ничего не найдено"

    parts, total = [], 0
    for r in items:
        src = _domain(r.get("href") or r.get("url") or "") or r.get("source") or "?"
        date = f" ({r['date'][:10]})" if r.get("date") else ""
        piece = f"[{src}]{date} {r.get('title', '')}: {r.get('body', '')}".strip(" :")
        piece = piece[:max_chars - total]
        if not piece:
            break
        parts.append(piece)
        total += len(piece)
        if total >= max_chars:
            break
    return "\n".join(parts), None


if __name__ == "__main__":
    # Проверка скорости поисковых движков:  python tools.py "цены на книгу Панасенкова"
    import sys
    q = " ".join(sys.argv[1:]) or "цены на книгу Панасенкова Первая научная история войны 1812 года"
    print(f"Запрос: {q}\n")
    for be in ["auto", "duckduckgo", "bing", "brave", "yandex", "mojeek"]:
        t = time.time()
        text, err = web_search(q, backend=be, timeout=12)
        shown = (text or f"ОШИБКА: {err}")[:140].replace("\n", " ")
        print(f"{be:11s} {time.time() - t:5.1f} с   {shown}")
