"""Чтение настроек из config.toml и (необязательно) профиля из папки profiles/."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent
MODELS_DIR = ROOT / "models"


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(profile: str | None = None) -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    if profile:
        path = ROOT / "profiles" / f"{profile}.toml"
        if not path.exists():
            raise SystemExit(f"Профиль '{profile}' не найден: {path}")
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    return cfg
