# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import threading
import queue
import socket
import traceback
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
logger = logging.getLogger("task")

def setup_file_logging(debug: bool = False) -> Path:
    # 跟 config.py 的 _config_dir 同目录体系
    import os, sys
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    log_dir = Path(base) / "MinecraftWikiAssistant" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"

    level = logging.DEBUG if debug else logging.INFO

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(fmt)
    handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    # 避免重复添加 handler（热重载/多次启动）
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)

    # 让 uvicorn / fastapi 也走同一个 handler
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True

    # 把 print 也导进日志（可选但推荐）
    class _PrintToLog:
        def __init__(self, logger, level):
            self.logger = logger
            self.level = level
        def write(self, msg):
            msg = msg.strip()
            if msg:
                self.logger.log(self.level, msg)
        def flush(self):  # required
            pass

    sys.stdout = _PrintToLog(logging.getLogger("stdout"), level)
    sys.stderr = _PrintToLog(logging.getLogger("stderr"), logging.ERROR)

    logging.getLogger("server").info("logging initialized: %s", str(log_path))
    return log_path
def _parse_port():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--port", type=int, default=None)
    args, _ = p.parse_known_args()
    if args.port is not None:
        return args.port
    return int(os.environ.get("BACKEND_PORT", "8000"))

PORT = _parse_port()
def app_root():
    import sys, os
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)   # server.exe 所在目录
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

ROOT_DIR = app_root()
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from backend.backend import run_pipeline
from config import load_config, save_config


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PORT = int(os.environ.get("BACKEND_PORT", "8000"))

os.environ.setdefault("HF_HOME", os.path.join(ROOT_DIR, ".hf_cache"))
os.environ.setdefault("TRANSFORMERS_CACHE", os.path.join(ROOT_DIR, ".hf_cache", "transformers"))
_tasks_lock = threading.Lock()
_tasks: Dict[str, "TaskState"] = {}
# --- warmup state ---
_warmup_lock = threading.Lock()
_warmup_state: Dict[str, Any] = {
    "started": False,
    "done": False,
    "error": None,
    "t0": None,
    "t1": None,
    "message": "",
}

_runtime_key_lock = threading.Lock()
_runtime_api_key: Optional[str] = None

def _set_runtime_api_key(k: Optional[str]) -> None:
    global _runtime_api_key
    with _runtime_key_lock:
        _runtime_api_key = (k or "").strip() or None

def _get_runtime_api_key() -> Optional[str]:
    with _runtime_key_lock:
        return _runtime_api_key
    
def _warmup_progress(msg: str):
    with _warmup_lock:
        _warmup_state["message"] = msg

def _warmup_worker():
    with _warmup_lock:
        _warmup_state["started"] = True
        _warmup_state["done"] = False
        _warmup_state["error"] = None
        _warmup_state["t0"] = time.time()
        _warmup_state["t1"] = None
        _warmup_state["message"] = "warming up..."

    try:
        # 这里触发 HF 下载/加载
        from backend import rag_cli
        _warmup_progress("loading rag_cli...")
        rag_cli.init_once()
        _warmup_progress("ready")
    except Exception as e:
        with _warmup_lock:
            _warmup_state["error"] = str(e)
            _warmup_state["message"] = "failed"
    finally:
        with _warmup_lock:
            _warmup_state["done"] = True
            _warmup_state["t1"] = time.time()

class TaskState:
    def __init__(self, session_id: str, message_id: str):
        self.session_id = session_id
        self.message_id = message_id
        self.queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.done = False
        self.error: Optional[str] = None


def _task_key(session_id: str, message_id: str) -> str:
    return f"{session_id}::{message_id}"


def _schedule_cleanup(key: str) -> None:
    def _cleanup():
        with _tasks_lock:
            _tasks.pop(key, None)
    t = threading.Timer(60.0, _cleanup)
    t.daemon = True
    t.start()


def _start_task(session_id: str, message_id: str, text: str, config: Dict[str, Any]) -> None:
    state = TaskState(session_id, message_id)
    key = _task_key(session_id, message_id)
    with _tasks_lock:
        _tasks[key] = state

    def runner():
        last_progress_emit = 0.0
        pending_text: Optional[str] = None
        last_answer_emit = 0.0
        pending_answer: str = ""

        def _emit_progress(txt: str, force: bool = False):
            nonlocal last_progress_emit, pending_text
            now = time.time()
            pending_text = txt
            if force or (now - last_progress_emit) >= 0.05:
                last_progress_emit = now
                state.queue.put({"event": "progress", "data": {"text": pending_text}})
                pending_text = None

        def progress_cb(msg: str):
            _emit_progress(msg, force=False)

        def _emit_answer_delta(txt: str, force: bool = False):
            nonlocal last_answer_emit, pending_answer
            now = time.time()
            pending_answer += txt
            if force or (now - last_answer_emit) >= 0.05:
                last_answer_emit = now
                state.queue.put({"event": "answer_delta", "data": {"delta": pending_answer}})
                pending_answer = ""

        def answer_stream_cb(chunk: str):
            _emit_answer_delta(chunk, force=False)

        try:
            from backend import rag_cli
            import sys
            print("[DEBUG] rag_cli.__file__ =", getattr(rag_cli, "__file__", None))
            print("[DEBUG] has pipeline =", hasattr(rag_cli, "pipeline"))
            print("[DEBUG] sys.path[0:5] =", sys.path[:5])
            result = run_pipeline(
                text,
                config,
                progress_cb=progress_cb,
                answer_stream_cb=answer_stream_cb,
            )
            if pending_text:
                state.queue.put({"event": "progress", "data": {"text": pending_text}})
            if pending_answer:
                state.queue.put({"event": "answer_delta", "data": {"delta": pending_answer}})
            final_data = {
                "answer": result.get("answer", ""),
                "evidences_for_llm": result.get("evidences_for_llm") or [],
                "token_usage": result.get("token_usage") or {},
                "timing_ms": result.get("timing_ms") or {},
            }
            state.queue.put({"event": "final", "data": final_data})
        except Exception as e:
            state.error = str(e)
            tb = traceback.format_exc()
            print("[task_error]", tb)  # 关键：把真实栈打印出来，立刻定位
            logger.exception("pipeline crashed")
            state.queue.put({"event": "backend_error", "data": {"error": str(e)}})
        finally:
            state.done = True
            _schedule_cleanup(key)

    t = threading.Thread(target=runner, daemon=True)
    t.start()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    cfg = load_config()
    has_key = bool(_get_runtime_api_key() or cfg.get("api_key"))
    # 永远不回传 api_key
    safe = dict(cfg)
    safe["api_key"] = ""
    safe["has_api_key"] = has_key
    return JSONResponse(safe)

@app.post("/api/config")
def update_config(payload: Dict[str, Any]):
    payload = payload or {}

    # 是否允许把 api_key 落盘（默认 false，更安全）
    persist_api_key = bool(payload.get("persist_api_key", False))

    cfg = load_config()

    # 1) 先处理 api_key：运行期内存保存（不回显、不默认落盘）
    api_key = (payload.get("api_key") or "").strip()
    if api_key:
        _set_runtime_api_key(api_key)

    # 2) 更新其它配置项（把 api_key/persist 字段剔除）
    for k in ["api_key", "persist_api_key"]:
        payload.pop(k, None)

    cfg.update(payload)

    # 3) 落盘（是否写入 api_key 取决于 persist_api_key）
    # 如果你用的是我之前给的 config.py：save_config(cfg, persist_api_key=...)
    save_config(cfg)

    return JSONResponse({"ok": True, "has_api_key": bool(_get_runtime_api_key() or cfg.get("api_key"))})

@app.post("/api/warmup")
def warmup():
    # 幂等：如果已经开始过就直接返回状态
    with _warmup_lock:
        started = bool(_warmup_state["started"])
        done = bool(_warmup_state["done"])
        err = _warmup_state["error"]
        msg = _warmup_state.get("message", "")

    if started and not done:
        return {"ok": True, "status": "running", "message": msg}
    if started and done and not err:
        return {"ok": True, "status": "ready", "message": msg}
    if started and done and err:
        return {"ok": False, "status": "failed", "error": err}

    # 还没启动：开线程
    t = threading.Thread(target=_warmup_worker, daemon=True)
    t.start()
    return {"ok": True, "status": "started"}


@app.get("/api/warmup_status")
def warmup_status():
    with _warmup_lock:
        st = dict(_warmup_state)
    # 计算耗时
    t0 = st.get("t0")
    t1 = st.get("t1")
    if t0:
        st["elapsed_sec"] = (t1 or time.time()) - t0
    else:
        st["elapsed_sec"] = 0.0
    return st

@app.post("/api/send")
def send(payload: Dict[str, Any]):
    session_id = payload.get("session_id", "default")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    config = load_config()

    # ✅ 方案B：从请求体拿 api_key（不落盘）
    api_key = (payload.get("api_key") or "").strip()
    if api_key:
        config["api_key"] = api_key

    if not config.get("api_key"):
        raise HTTPException(status_code=400, detail="api_key required")

    message_id = f"{int(time.time() * 1000)}"
    _start_task(session_id, message_id, text, config)
    return {"message_id": message_id}
@app.get("/api/debug_config_paths")
def debug_config_paths():
    import config as _cfgmod
    import os
    from pathlib import Path
    return {
        "config_py": _cfgmod.__file__,
        "config_path": str(getattr(_cfgmod, "CONFIG_PATH", "")),
        "APPDATA": os.environ.get("APPDATA"),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
        "HOME": str(Path.home()),
        "CWD": os.getcwd(),
    }
from pathlib import Path
import os
@app.get("/api/debug/env")
def debug_env():
    return {"MWA_DATA_DIR": os.environ.get("MWA_DATA_DIR")}
@app.get("/api/log_dir")
def log_dir():
    base = os.environ.get("MWA_DATA_DIR") or os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / "MinecraftWikiAssistant" / "logs"
    # ✅ Windows 下把反斜杠统一成正斜杠，便于和 capability 匹配
    return {"log_dir": p.as_posix()}
@app.get("/api/stream")
def stream(session_id: str, message_id: str):
    key = _task_key(session_id, message_id)
    with _tasks_lock:
        state = _tasks.get(key)
    if not state:
        raise HTTPException(status_code=404, detail="task not found")

    def event_gen():
        # Initial comment ping helps some clients keep the connection alive.
        yield ": ping\n\n"
        while True:
            try:
                item = state.queue.get(timeout=1.0)
            except queue.Empty:
                if state.done:
                    break
                continue

            event = item.get("event")
            data = item.get("data")
            raw = json.dumps(data, ensure_ascii=False)
            print(f"[sse] event={event} bytes={len(raw.encode('utf-8'))}")
            payload = f"event: {event}\n" + f"data: {raw}\n\n"
            payload = f"event: {event}\n" + f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            yield payload

            if event in ("final", "backend_error"):
                break

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        # Some proxies buffer SSE; this header disables buffering when supported.
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)
@app.on_event("startup")
def _on_startup():
    # 后端启动即 warmup（不阻塞启动）
    with _warmup_lock:
        already = bool(_warmup_state["started"])
    if not already:
        threading.Thread(target=_warmup_worker, daemon=True).start()

if __name__ == "__main__":
    import uvicorn
    import traceback
    from uvicorn import Config, Server
    from logging_utils import setup_file_logging

    cfg = load_config()
    debug_mode = bool(cfg.get("debug_mode", False))

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", PORT))
    server_socket.listen(2048)
    actual_port = int(server_socket.getsockname()[1])

    # Rust 侧通过 stdout 捕获实际监听端口。
    print(f"PORT={actual_port}", flush=True)

    log_path = setup_file_logging(debug=debug_mode)

    import logging
    logging.getLogger("server").info("about to start uvicorn on 127.0.0.1:%s...", actual_port)

    try:
        config = Config(
            app,
            host="127.0.0.1",
            port=actual_port,
            log_level=("debug" if debug_mode else "info"),
            access_log=True,
            log_config=None,
        )
        Server(config).run(sockets=[server_socket])
    except Exception:
        # 1) 写到 server.log（如果 logging 可用）
        logging.getLogger("server").exception("uvicorn.run crashed")

        # 2) 再写一份 fatal.log（就算 logging 坏了也能看到）
        fatal = log_path.parent / "fatal.log"
        fatal.write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        try:
            server_socket.close()
        except Exception:
            pass
