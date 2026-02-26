# logging_utils.py
from __future__ import annotations
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
from config import logs_dir  # ✅ 统一入口

def data_root_dir() -> Path:
    # ✅ 优先使用 Tauri 传入的 app_data_dir
    d = os.environ.get("MWA_DATA_DIR")
    if d:
        p = Path(d)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # fallback：纯 python 运行时
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / "MinecraftWikiAssistant"
    p.mkdir(parents=True, exist_ok=True)
    return p
# logging_utils.py

def setup_file_logging(debug: bool = False) -> Path:
    log_dir = logs_dir()
    log_path = log_dir / "server.log"

    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()  # ✅ 避免重复/冲突（简单粗暴但稳定）
    root.addHandler(handler)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True

    logging.getLogger("server").info("logging initialized: %s", str(log_path))
    return log_path