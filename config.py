# config.py
# -*- coding: utf-8 -*-
import json
import os
from pathlib import Path
from typing import Dict, Any

APP_NAME_FALLBACK = "MinecraftWikiAssistant"

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",  # 只用于运行时，不落盘
    "api_base": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "cache_hit_rate": 0.07,
    "input_hit_per_million": 0.2,
    "input_miss_per_million": 2.0,
    "output_per_million": 3.0,
    "font_size": 14,
    "material_mode": "liquid",
    "debug_mode": False,
    "log_level": "info",
}

def data_root_dir() -> Path:
    d = os.environ.get("MWA_DATA_DIR")
    if not d:
        raise RuntimeError("MWA_DATA_DIR not provided")
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p

def config_path() -> Path:
    return data_root_dir() / "config.json"

def logs_dir() -> Path:
    d = data_root_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d

def migrate_legacy_if_needed() -> None:
    new_cfg = config_path()
    if new_cfg.exists():
        return

    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    legacy_root = Path(base) / "MinecraftWikiAssistant"
    legacy_cfg = legacy_root / "config.json"

    if legacy_cfg.exists():
        try:
            new_cfg.write_text(legacy_cfg.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

def load_config() -> Dict[str, Any]:
    migrate_legacy_if_needed()

    cfg = dict(DEFAULT_CONFIG)
    p = config_path()

    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass

    env_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("API_KEY")
    if env_key:
        cfg["api_key"] = env_key.strip()

    return cfg

def save_config(cfg: Dict[str, Any]) -> None:
    safe = dict(cfg)
    safe.pop("api_key", None)

    p = config_path()
    p.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
