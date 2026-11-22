"""
Языковая модель: локальная (Ollama) или облачная (любой OpenAI-совместимый сервис).
Всё остальное в проекте не знает, какая модель работает: оно просто просит ответ.
Ключ облачного сервиса берётся из переменной окружения и никогда не пишется в файлы.
"""
import json
import os
import time
import urllib.request


class OllamaLLM:
    kind = "ollama"

    def __init__(self, cfg: dict, model: str | None = None):
        import ollama
        self.ollama = ollama
        b = cfg["brain"]
        self.model = model or b["model"]
        self.num_ctx = int(b["num_ctx"])
        self.keep_alive = b["keep_alive"]
        self.truncated = False
        self._check_model()

    def _check_model(self) -> None:
        try:
            models = self.ollama.list()["models"]
            names = [(m.get("model") or m.get("name") or "") for m in models]
        except Exception as e:
            raise SystemExit(f"[МОЗГ] Ollama не отвечает ({e}).\n"
                             "Запусти программу Ollama (значок в трее) и повтори.")
        if not any(n == self.model or n.startswith(self.model) for n in names):
            raise SystemExit(f"[МОЗГ] Модель '{self.model}' не скачана.\n"
                             f"Выполни:  ollama pull {self.model}\n"
                             f"Скачано сейчас: {', '.join(names) or 'ничего'}")

    def warmup(self) -> None:
        t = time.time()
        print(f"[МОЗГ] Загружаю {self.model} в видеопамять...")
        self.complete([{"role": "user", "content": "Скажи одно слово: готов."}], max_tokens=5)
        print(f"[МОЗГ] Готов за {time.time() - t:.1f} с")

    def stream(self, messages, temperature=0.8, max_tokens=120):
        kw = dict(model=self.model, messages=messages, stream=True, keep_alive=self.keep_alive,
                  options={"num_ctx": self.num_ctx, "temperature": temperature,
                           "num_predict": max_tokens})
        self.truncated = False
        for think_off in (True, False):
            args = dict(kw, think=False) if think_off else kw
            started = False
            try:
                for part in self.ollama.chat(**args):
                    msg = _field(part, "message")
                    piece = _field(msg, "content") if msg is not None else ""
                    if piece:
                        started = True
                        yield piece
                    if _field(part, "done_reason") == "length":
                        self.truncated = True
                return
            except (TypeError, self.ollama.ResponseError) as e:
                if think_off and not started and ("think" in str(e).lower()
                                                  or isinstance(e, TypeError)):
                    continue
                raise

    def complete(self, messages, temperature=0.2, max_tokens=200) -> str:
        return "".join(self.stream(messages, temperature, max_tokens)).strip()


class OpenAILLM:
    """Любой сервис с адресом вида .../v1/chat/completions (OpenRouter, DeepSeek и другие)."""
    kind = "openai"

    def __init__(self, cfg: dict):
        o = cfg.get("llm", {}).get("openai", {})
        self.base = o.get("base_url", "").rstrip("/")
        self.model = o.get("model", "")
        key_env = o.get("api_key_env", "ENERGIK_API_KEY")
        self.key = os.environ.get(key_env, "")
        self.truncated = False
        if not (self.base and self.model and self.key):
            raise SystemExit(f"[МОЗГ] Облачный режим не настроен: нужны base_url и model в [llm.openai] "
                             f"и переменная окружения {key_env} с ключом.")

    def warmup(self) -> None:
        print(f"[МОЗГ] Облачная модель: {self.model} ({self.base})")

    def stream(self, messages, temperature=0.8, max_tokens=120):
        body = json.dumps({"model": self.model, "messages": messages, "stream": True,
                           "temperature": temperature, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(self.base + "/chat/completions", data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
        self.truncated = False
        with urllib.request.urlopen(req, timeout=40) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    choice = json.loads(data)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece
                if choice.get("finish_reason") == "length":
                    self.truncated = True

    def complete(self, messages, temperature=0.2, max_tokens=200) -> str:
        return "".join(self.stream(messages, temperature, max_tokens)).strip()


def _field(obj, name):
    try:
        return obj[name]
    except Exception:
        return getattr(obj, name, None)


def make(cfg: dict):
    """Возвращает (диалоговая модель, служебная модель). Служебная всегда локальная по умолчанию:
    решения «нужно ли искать», пересказ страниц и записи в память не уходят в облако."""
    l = cfg.get("llm", {})
    local = OllamaLLM(cfg)
    dialogue = OpenAILLM(cfg) if l.get("dialogue", "ollama") == "openai" else local
    utility = OpenAILLM(cfg) if l.get("utility", "ollama") == "openai" else local
    return dialogue, utility
