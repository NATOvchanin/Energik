"""
Любопытство Энергика: сам решает, нужно ли ему что-то узнать, и сам идёт читать.
Никаких слов-команд: модель смотрит на реплику Босса и недавний разговор и отвечает на вопрос
«мне надо это выяснить, чтобы не соврать?».
"""
import re

import memory
import tools

DECIDE_PROMPT = """Ты решаешь, нужно ли Мистеру Энергику посмотреть в интернете, чтобы ответить Боссу и не соврать.
Ответ строго в JSON: {"action":"none"|"who"|"search"|"news","query":"..."}

none: болтовня, шутки, мнения, вопросы про самого Энергика, про память и про этот разговор, вопросы «как тебе...», «что думаешь...», общие знания и математика, продолжение темы без новых фактов.
who: Босс спрашивает, кто такой человек или что такое малоизвестная вещь, название, термин, проект.
search: нужны свежие или точные факты, которых модель не знает: цены, даты и результаты событий, характеристики конкретных товаров, что вышло недавно, где что находится, просьба что-то найти.
news: Босс хочет узнать, что происходит в мире.
query: короткий поисковый запрос на русском (2-8 слов), с названиями из разговора, без слов «пожалуйста» и «загугли».

Примеры:
Босс: Я хочу пиво. -> {"action":"none","query":""}
Босс: Как думаешь, кто победит, Спартак или ЦСКА? -> {"action":"none","query":""}
Босс: А кто вообще такой этот Илья Мэдисон? -> {"action":"who","query":"Илья Мэдисон"}
Босс: Сколько сейчас стоит видеокарта 5060 Ти? -> {"action":"search","query":"цена видеокарта RTX 5060 Ti"}
Босс: Вышла уже вторая часть Subnautica? -> {"action":"search","query":"Subnautica 2 дата выхода"}
Босс: Чему равен корень из ста сорока четырёх? -> {"action":"none","query":""}
Босс: Что там в мире творится? -> {"action":"news","query":"новости"}
Босс: Расскажи анекдот. -> {"action":"none","query":""}
Босс: Слушай, а что за игра Disco Elysium? -> {"action":"who","query":"Disco Elysium игра"}
Босс: Как тебя зовут? -> {"action":"none","query":""}
Босс: Ты помнишь, что я тебе рассказывал вчера? -> {"action":"none","query":""}
Босс: Ты хотел бы увидеть, что у меня на мониторе? -> {"action":"none","query":""}
Босс: Как тебе игра Fallout 3? -> {"action":"none","query":""}
Босс: А почему ты такой вредный? -> {"action":"none","query":""}

Недавний разговор:
{context}

Реплика Босса: {text}
JSON:"""

# Сайты, которые рисуют содержимое скриптами или не пускают роботов: читать их страницы бессмысленно
NO_READ = ("avito.", "ozon.", "wildberries.", "market.yandex.", "aliexpress.", "youtube.", "youtu.be",
           "vk.com", "t.me", "instagram.", "facebook.", "tiktok.", "x.com", "twitter.")

SUMMARIZE_PROMPT = """Вопрос Босса: {question}
Запрос: {query}

Ниже текст страницы. Выпиши до 4 фактов, которые отвечают на вопрос. Числа, цены, даты и названия бери дословно. Ничего не выдумывай. Если ответа в тексте нет, напиши одно слово: НЕТ.

Текст:
{page}

Факты:"""


def _flag_missing_price(facts: str, question: str) -> str:
    """Если Босс спрашивал про цену, а в найденном нет ни одной цены, говорим об этом прямо."""
    asks_price = re.search(r"цен|стоит|сколько|стоимост", question, re.I)
    has_price = re.search(r"\d[\d\s.,]*\s*(?:₽|руб|р\.|тыс)", facts, re.I)
    if asks_price and not has_price:
        facts += "\n(ВНИМАНИЕ: в найденном нет ни одной цены. Так и скажи Боссу, цифр не придумывай.)"
    return facts


def decide(llm, user_text: str, history: list[dict]):
    """Возвращает (вид, запрос) или None. Вид: 'who', 'search', 'news'."""
    words = re.findall(r"\w+", user_text)
    if len(words) < 3:
        return None  # «ну», «ещё», «угу»: тут думать не о чем
    ctx = "\n".join(("Босс: " if m["role"] == "user" else "Энергик: ") + m["content"][:200]
                    for m in history[-4:]) or "(разговор только начался)"
    prompt = DECIDE_PROMPT.replace("{context}", ctx).replace("{text}", user_text)
    try:
        raw = llm.complete([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=60)
    except Exception as e:
        print(f"[ЛЮБОПЫТСТВО] решатель не ответил: {e}")
        return None
    data = memory.parse_json_object(raw)
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "none")).lower()
    query = re.sub(r"\s+", " ", str(data.get("query", ""))).strip()
    if re.search(r"энергик|тебя зовут|как тебя", query, re.I):
        return None  # про самого себя Энергик знает без интернета
    if action in ("who", "search", "news") and (len(query) >= 3):
        return action, query
    return None


def research(llm, kind: str, query: str, question: str, cfg: dict):
    """
    Идёт в сеть, читает страницу целиком и выжимает факты.
    Возвращает (факты, None) или (None, причина).
    """
    b = cfg["brain"]
    timeout = float(b.get("search_timeout", 10))
    backend = b.get("search_backend", "auto")

    if kind == "who":
        found, err = tools.wiki_summary(query, timeout=min(timeout, 6), max_chars=1200)
        if found:
            return found, None
        print(f"[ЛЮБОПЫТСТВО] Википедия не помогла ({err}), ищу в сети")

    items, err = tools.search_items(query, "text", max_results=5, timeout=timeout, backend=backend)
    if not items:
        return None, err

    # пробуем прочитать страницы целиком (до трёх), иначе довольствуемся выдержками
    for it in items[:4]:
        if not it["href"] or any(d in it["source"] for d in NO_READ):
            continue
        print(f"[ЛЮБОПЫТСТВО] читаю страницу: {it['source']}")
        page, perr = tools.fetch_page_text(it["href"], timeout=6, max_chars=2600)
        if not page:
            print(f"[ЛЮБОПЫТСТВО]   не вышло: {perr}")
            continue
        try:
            facts = llm.complete([{"role": "user", "content": SUMMARIZE_PROMPT.format(
                question=question[:300], query=query, page=page)}], temperature=0.1, max_tokens=220)
        except Exception as e:
            print(f"[ЛЮБОПЫТСТВО] не вышло пересказать: {e}")
            break
        if facts and not facts.strip().upper().startswith("НЕТ"):
            return _flag_missing_price(f"[{it['source']}] {facts.strip()}", question), None
    return _flag_missing_price(tools.format_items(items[:4], 900), question), None
