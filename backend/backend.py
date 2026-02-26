# -*- coding: utf-8 -*-
import os
import time
from typing import Dict, Any, Callable, Optional


def run_pipeline(
    question: str,
    config: Dict[str, Any],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    t0 = time.time()

    from backend import rag_cli

    kwargs: Dict[str, Any] = {
        "progress_cb": progress_cb,
        "config": config,  # ✅ 新增：把 config 直接传进去
    }

    # 这些 key 只有存在才传，避免覆盖 rag_cli 默认行为
    if config.get("vec_k") is not None:
        kwargs["vec_k"] = int(config["vec_k"])
    if config.get("top_k") is not None:
        kwargs["top_k"] = int(config["top_k"])

    if config.get("max_evidences") is not None:
        kwargs["max_evidences"] = int(config["max_evidences"])
    if config.get("max_chars_per_evidence") is not None:
        kwargs["max_chars_per_evidence"] = int(config["max_chars_per_evidence"])

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

    # ✅ 调用 rag_cli.pipeline（现在它必须支持 config 参数）
    result = rag_cli.pipeline(question, **kwargs)

    t1 = time.time()

    return {
        "answer": result.get("answer", ""),
        "debug": result.get("debug", {}),
        "stats": result.get("stats"),
        "token_usage": result.get("token_usage") or {},
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