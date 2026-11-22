"""Инструменты Энергика: поиск в интернете (необязательный, без сети он просто не работает)."""
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

# --- как понять, что Босс просит что-то найти ---
_VERBS = r"(?:за|по)?гугл\w*|поищ\w*|найд\w*|найти|откро\w*|открыть|посмотр\w*|погляд\w*|глян\w*"
_WHERE = r"в\s+(?:интернет\w*|сети|гугле|яндексе)"
GOOGLE_RE = re.compile(r"\b(?:за|по)?гугл\w*", re.I)
VERB_RE = re.compile(r"\b(?:" + _VERBS + r")", re.I)
WHERE_RE = re.compile(_WHERE, re.I)
WHO_RE = re.compile(r"\bкто\s+так(?:ой|ая|ие|ое)\b", re.I)
NEWS_ASK_RE = re.compile(r"\b(?:какие\s+новости|последние\s+новости|что\s+(?:там\s+)?(?:в|по)\s+новост\w*)", re.I)
BARE_NEWS_RE = re.compile(r"^\W*(?:ну\s+)?новост\w*\W*$", re.I)
NEWS_VERBS_RE = re.compile(r"\b(?:" + _VERBS + r"|расскаж\w*|скаж\w*|покаж\w*|дай|давай|включи\w*|читай|прочита\w*|почита\w*|выдай)", re.I)
MORE_RE = re.compile(r"^\W*(?:ну\s+|а\s+|давай\s+)?(?:ещё|еще|дальше|следующ\w+|друг(?:ую|ой))\b", re.I)
MORE_ANY_RE = re.compile(r"то\s+же\s+самое|ещё\s+одну|еще\s+одну|следующую|давай\s+дальше", re.I)
NEWS_RE = re.compile(r"новост\w*", re.I)
LEAD_RE = re.compile(r"^(?:мне|пожалуйста|ка|ну|там|что-нибудь|что-то|про|о|об)\s+", re.I)
STOP_WORD_RE = re.compile(r"^(?:и|а|но|или|да)$|^(?:" + _VERBS + r")$", re.I)


def is_more_request(text: str) -> bool:
    """«ещё», «дальше», «то же самое»: просьба продолжить недавние новости."""
    s = text.strip()
    return len(s) < 45 and bool(MORE_RE.match(s) or MORE_ANY_RE.search(s))


def parse_search_request(text: str):
    """
    Возвращает (вид, запрос): вид 'news' (новости), 'who' («кто такой») или 'text' (обычный поиск).
    None, если искать не просили.
    """
    if BARE_NEWS_RE.match(text) or NEWS_ASK_RE.search(text):
        return "news", "новости"
    m_g, m_who = GOOGLE_RE.search(text), WHO_RE.search(text)
    m_v, m_nv = VERB_RE.search(text), NEWS_VERBS_RE.search(text)
    kind = "text"
    if m_g:
        start = m_g.end()
    elif m_who:
        start, kind = m_who.end(), "who"
    elif m_v and WHERE_RE.search(text):
        start = m_v.end()
    elif m_nv and NEWS_RE.search(text):
        start = m_nv.end()
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
    return (kind, query) if len(query) >= 3 else None


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


def search_items(query: str, kind: str = "text", max_results: int = 5, timeout: float = 7.0,
                 backend: str = "auto"):
    """Возвращает (список результатов, None) или ([], причина). Результат: title, body, href, source, date."""
    query = query.strip()
    if len(query) < 3:
        return [], "запрос слишком короткий"
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
        return [], f"поиск не ответил за {timeout:.0f} с"
    if "err" in box:
        return [], box["err"]
    items = box.get("items") or []
    if not items:
        return [], "ничего не найдено"
    out = []
    for r in items:
        href = r.get("href") or r.get("url") or ""
        out.append({"title": r.get("title", ""), "body": r.get("body", ""), "href": href,
                    "source": _domain(href) or r.get("source") or "?", "date": (r.get("date") or "")[:10]})
    return out, None


def format_items(items: list, max_chars: int = 900) -> str:
    parts, total = [], 0
    for r in items:
        date = f" ({r['date']})" if r.get("date") else ""
        piece = f"[{r['source']}]{date} {r['title']}: {r['body']}".strip(" :")[:max_chars - total]
        if not piece:
            break
        parts.append(piece)
        total += len(piece)
        if total >= max_chars:
            break
    return "\n".join(parts)


def web_search(query: str, kind: str = "text", max_results: int = 4, max_chars: int = 900,
               timeout: float = 7.0, backend: str = "auto"):
    """Возвращает (текст, None) при успехе или (None, причина) при неудаче."""
    items, err = search_items(query, kind, max_results, timeout, backend)
    if not items:
        return None, err
    return format_items(items, max_chars), None


# --- быстрые источники: Википедия и ленты новостей ---
_UA = {"User-Agent": "Mozilla/5.0 (Energik voice assistant)"}


def _get(url: str, timeout: float = 6) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
        return r.read()


def wiki_summary(query: str, lang: str = "ru", timeout: float = 6, max_chars: int = 700):  # max_chars до 1200
    """Быстрая справка для вопросов «кто такой ...». Возвращает (текст, None) или (None, причина)."""
    url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&generator=search&gsrlimit=1"
           f"&prop=extracts&exintro=1&explaintext=1&redirects=1&format=json&exchars={max_chars}"
           f"&gsrsearch={urllib.parse.quote(query)}")
    try:
        data = json.loads(_get(url, timeout))
    except Exception as e:
        return None, str(e)[:100] or type(e).__name__
    pages = (data.get("query") or {}).get("pages") or {}
    if not pages:
        return None, "в Википедии ничего не нашлось"
    page = next(iter(pages.values()))
    text = (page.get("extract") or "").strip()
    title = page.get("title", "")
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    if not text or (words and not any(w[:5] in (title + " " + text).lower() for w in words)):
        return None, "статья в Википедии не похожа на запрос"  # защита от случайного совпадения
    return f"[{lang}.wikipedia.org] {title}: {text}", None


def fetch_news(feeds: list, per_feed: int = 8, timeout: float = 6):
    """Читает RSS-ленты. Возвращает (список новостей, None) или ([], причина)."""
    items, errs, seen = [], [], set()
    for url in feeds:
        try:
            root = ET.fromstring(_get(url, timeout))
        except Exception as e:
            errs.append(f"{_domain(url)}: {str(e)[:50]}")
            continue
        n = 0
        for it in root.iter("item"):
            title = html.unescape((it.findtext("title") or "").strip())
            if not title or title in seen:
                continue
            seen.add(title)
            desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
            desc = html.unescape(re.sub(r"\s+", " ", desc)).strip()
            try:
                dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            except Exception:
                dt = None
            items.append({"title": title, "summary": desc[:300], "source": _domain(url),
                          "ts": dt.timestamp() if dt else 0,
                          "date": dt.strftime("%d.%m %H:%M") if dt else ""})
            n += 1
            if n >= per_feed:
                break
    if not items:
        return [], "; ".join(errs) or "ленты пустые"
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items, None


# --- чтение страниц ---
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"}
_TEXT_TAGS = {"p", "h1", "h2", "h3", "li", "blockquote"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.cur = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _TEXT_TAGS and not self.skip:
            self.cur = []

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in _TEXT_TAGS and self.cur is not None:
            text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
            if len(text) >= 40:
                self.parts.append(text)
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None and not self.skip:
            self.cur.append(data)


def html_to_text(raw_html: str, max_chars: int = 3500) -> str:
    p = _TextExtractor()
    try:
        p.feed(raw_html)
    except Exception:
        pass
    return "\n".join(p.parts)[:max_chars]


def fetch_page_text(url: str, timeout: float = 6, max_chars: int = 3500):
    """Скачивает страницу и вытаскивает основной текст. Возвращает (текст, None) или (None, причина)."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(500_000)
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception as e:
        return None, str(e)[:80] or type(e).__name__
    try:
        doc = raw.decode(charset, "ignore")
    except LookupError:
        doc = raw.decode("utf-8", "ignore")
    text = html_to_text(doc, max_chars)
    return (text, None) if len(text) >= 300 else (None, "на странице мало текста")


if __name__ == "__main__":
    # Проверка скорости поисковых движков:  python tools.py "цены на книгу Панасенкова"
    import sys
    if "--news" in sys.argv:
        feeds = ["https://lenta.ru/rss/news", "https://www.interfax.ru/rss.asp", "https://tass.ru/rss/v2.xml"]
        for f in feeds:
            t0 = time.time()
            items, err = fetch_news([f])
            print(f"{_domain(f):16s} {time.time() - t0:4.1f} с  ",
                  f"{len(items)} новостей, первая: {items[0]['title'][:70]}" if items else f"ОШИБКА: {err}")
        sys.exit()
    if "--wiki" in sys.argv:
        q = " ".join(a for a in sys.argv[1:] if a != "--wiki") or "рене декарт"
        t0 = time.time()
        text, err = wiki_summary(q)
        print(f"{time.time() - t0:.1f} с", (text or f"ОШИБКА: {err}")[:300])
        sys.exit()
    q = " ".join(sys.argv[1:]) or "цены на книгу Панасенкова Первая научная история войны 1812 года"
    print(f"Запрос: {q}\n")
    for be in ["auto", "duckduckgo", "bing", "brave", "yandex", "mojeek"]:
        t = time.time()
        text, err = web_search(q, backend=be, timeout=12)
        shown = (text or f"ОШИБКА: {err}")[:140].replace("\n", " ")
        print(f"{be:11s} {time.time() - t:5.1f} с   {shown}")
