# -*- coding: utf-8 -*-
import os
import time
from typing import Dict, Any, Callable, Optional


def run_pipeline(
    question: str,
    config: Dict[str, Any],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    单一真相：直接调用 rag_cli.pipeline()，避免 GUI / CLI 分叉。
    """
    t0 = time.time()

    # ---- 1) 仅负责注入 LLM 环境变量 ----
    api_key = (config.get("api_key") or "").strip()
    api_base = (config.get("api_base") or "").strip()
    model = (config.get("model") or "").strip()

    if api_key:
        os.environ["LLM_API_KEY"] = api_key
    if api_base:
        os.environ["LLM_API_BASE"] = api_base
    if model:
        os.environ["LLM_MODEL"] = model

    import rag_cli

    # ---- 2) 从 config 读取可选参数（没配就让 rag_cli 用自己的默认值） ----
    # 你要“30->20、600->900”这类策略，建议就写进 config，
    # pipeline 里只负责透传；没写就完全按 rag_cli 默认走。
    kwargs: Dict[str, Any] = {
        "progress_cb": progress_cb,
    }

    # 这些 key 只有存在才传，避免覆盖 rag_cli 的默认行为
    if config.get("vec_k") is not None:
        kwargs["vec_k"] = int(config["vec_k"])
    if config.get("top_k") is not None:
        kwargs["top_k"] = int(config["top_k"])

    if config.get("max_evidences") is not None:
        kwargs["max_evidences"] = int(config["max_evidences"])
    if config.get("max_chars_per_evidence") is not None:
        kwargs["max_chars_per_evidence"] = int(config["max_chars_per_evidence"])

    # trace 相关：一般前端不开；要开再在 config 里配
    if config.get("trace") is not None:
        kwargs["trace"] = bool(config["trace"])
    if config.get("trace_target") is not None:
        kwargs["trace_target"] = int(config["trace_target"])
    if config.get("trace_out") is not None:
        kwargs["trace_out"] = str(config["trace_out"])

    if config.get("eval_target_title") is not None:
        kwargs["eval_target_title"] = str(config["eval_target_title"])
    if config.get("eval_target_id") is not None:
        kwargs["eval_target_id"] = int(config["eval_target_id"])

    # ---- 3) 调用 rag_cli.pipeline（核心逻辑全部在 rag_cli） ----
    result = rag_cli.pipeline(question, **kwargs)

    # ---- 4) 兼容你原来的返回格式，同时把 pipeline 的关键信息带回去 ----
    t1 = time.time()

    # pipeline 已经有 stats / debug / evidences_*，这里不要再重复算 token
    # （否则你又引入第二套 token 估算逻辑，还是分叉）
    return {
        "answer": result.get("answer", ""),
        "debug": result.get("debug", {}),
        "stats": result.get("stats"),
        "token_usage": result.get("token_usage") or {},
        # 给前端：喂给 LLM 的证据（真正影响答案）
        "evidences_for_llm": [
            {
                "title": e.get("title"),
                "url": e.get("url"),
                "section_path": e.get("section_path"),
                "rank": e.get("rank"),
                "score": e.get("score"),
                "source": e.get("source"),
                "text_preview": (e.get("text") or "")[:300],
            }
            for e in (result.get("evidences_for_llm") or [])
        ],

        # 可选：也把 raw evidences 返回，方便你对比“为什么后端30、前端12”
        "evidences_raw": [
            {
                "title": e.get("title"),
                "url": e.get("url"),
                "section_path": e.get("section_path"),
                "rank": e.get("rank"),
                "score": e.get("score"),
                "source": e.get("source"),
                "text_preview": (e.get("text") or "")[:160],
            }
            for e in (result.get("evidences_raw") or [])
        ],

        "timing_ms": {"total": int((t1 - t0) * 1000)},
    }