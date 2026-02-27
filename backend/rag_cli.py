import sys
if any(a in ("-h", "--help") for a in sys.argv[1:]):
    print(
        "Usage:\n"
        "  python rag_cli.py --question \"...\" [--trace] [--trace-target 11602] [--vec-k 2000] [--top-k 80] [--trace-out trace.json]\n"
        "  python rag_cli.py --interactive\n"
    )
    raise SystemExit(0)
import json
import os
import threading
_INIT_LOCK = threading.Lock()
import re
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Optional
import faiss
import traceback
import requests
from sentence_transformers import SentenceTransformer
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "pyserver" / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
model = SentenceTransformer(str(MODEL_DIR))
INDEX_PATH = PROJECT_ROOT /"index" / "faiss_all.index"
META_PATH = PROJECT_ROOT /"index" /"meta_all.jsonl"
EVIDENCE_FOR_LLM = 30
EVIDENCE_TEXT_MAX = 600          # 普通段落
TRADE_EVIDENCE_TEXT_MAX = 2800   # 交易表段落（关键）
SECTION2IDXS: Dict[Tuple[str, int], List[int]] = {}
# ============================================================
# Token Estimate (DeepSeek rough)
# ============================================================
TOKEN_STATS = {
    "prompt_tokens": 0.0,
    "completion_tokens": 0.0,
    "prompt_chars_cn": 0,
    "prompt_chars_en": 0,
    "prompt_chars_other": 0,
    "completion_chars_cn": 0,
    "completion_chars_en": 0,
    "completion_chars_other": 0,
    "calls": defaultdict(int),
}
PRICE_RMB_PER_M = {
    "in_hit": 0.2,   # 1M tokens input (cache hit)
    "in_miss": 2.0,  # 1M tokens input (cache miss)
    "out": 3.0,      # 1M tokens output
}
CACHE_HIT_RATE = 0.07  # 7%
ASCII_EN_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
import re
from typing import Dict, List, Optional

_TRADE_LINE_RE = re.compile(r"(?m)^\s*交易\s*:\s*")
_TRADE_SECTION_HINT_RE = re.compile(r"(交易|trades?)", re.I)

def _is_trade_evidence(ev: Dict) -> bool:
    tx = (ev.get("text") or "")
    sp = (ev.get("section_path") or ev.get("section") or "")
    st = (ev.get("section_title") or "")
    # 1) 文本里有多条交易行
    if _TRADE_LINE_RE.search(tx):
        return True
    # 2) section 提示为“交易”
    if _TRADE_SECTION_HINT_RE.search(sp) or _TRADE_SECTION_HINT_RE.search(st):
        return True
    return False

def _truncate_trade_text(tx: str, limit: int) -> str:
    """
    交易证据截断策略：
    - 尽量从第一条“交易:”开始保留（跳过标题/模板/导言）
    - 保留开头少量信息（标题/小节），再拼交易主体
    """
    if len(tx) <= limit:
        return tx

    m = _TRADE_LINE_RE.search(tx)
    if not m:
        return tx[:limit] + "…"

    # 保留头部（标题/小节）最多 180 字符
    head = tx[:m.start()]
    head = head.strip()
    if len(head) > 180:
        head = head[:180].rstrip() + "\n…"

    body = tx[m.start():]
    # 让主体拿到尽可能多空间
    remain = max(0, limit - len(head) - 1)
    if remain <= 0:
        return (tx[:limit] + "…")
    body = body[:remain].rstrip() + "…"
    return (head + "\n" + body).strip()

def _truncate_text_general(tx: str, limit: int) -> str:
    return tx if len(tx) <= limit else (tx[:limit] + "…")
def _classify_char(ch: str) -> str:
    o = ord(ch)
    if 0x4E00 <= o <= 0x9FFF:
        return "cn"
    if 0x3000 <= o <= 0x303F:
        return "cn"  # CJK Symbols and Punctuation
    if 0xFF00 <= o <= 0xFFEF:
        return "cn"  # Fullwidth forms (incl. punctuation)
    if o < 128:
        if ch.isalnum() or ch in ASCII_EN_PUNCT or ch.isspace():
            return "en"
    return "other"
def estimate_tokens_with_counts(text: str) -> tuple:
    if not text:
        return 0.0, 0, 0, 0
    cn = en = other = 0
    for ch in text:
        t = _classify_char(ch)
        if t == "cn":
            cn += 1
        elif t == "en":
            en += 1
        else:
            other += 1
    tokens = cn * 0.6 + en * 0.3 + other * 1.0
    return tokens, cn, en, other
def _add_token_usage(call_name: str, system: str, user: str, assistant: str) -> None:
    TOKEN_STATS["calls"][call_name] += 1
    prompt_text = f"{system}\n{user}"
    p_tokens, p_cn, p_en, p_other = estimate_tokens_with_counts(prompt_text)
    c_tokens, c_cn, c_en, c_other = estimate_tokens_with_counts(assistant)
    TOKEN_STATS["prompt_tokens"] += p_tokens
    TOKEN_STATS["completion_tokens"] += c_tokens
    TOKEN_STATS["prompt_chars_cn"] += p_cn
    TOKEN_STATS["prompt_chars_en"] += p_en
    TOKEN_STATS["prompt_chars_other"] += p_other
    TOKEN_STATS["completion_chars_cn"] += c_cn
    TOKEN_STATS["completion_chars_en"] += c_en
    TOKEN_STATS["completion_chars_other"] += c_other
def reset_token_stats() -> None:
    TOKEN_STATS["prompt_tokens"] = 0.0
    TOKEN_STATS["completion_tokens"] = 0.0
    TOKEN_STATS["prompt_chars_cn"] = 0
    TOKEN_STATS["prompt_chars_en"] = 0
    TOKEN_STATS["prompt_chars_other"] = 0
    TOKEN_STATS["completion_chars_cn"] = 0
    TOKEN_STATS["completion_chars_en"] = 0
    TOKEN_STATS["completion_chars_other"] = 0
    TOKEN_STATS["calls"].clear()
def estimate_cost_rmb(prompt_tokens: float, completion_tokens: float) -> dict:
    p = float(prompt_tokens)
    c = float(completion_tokens)
    in_hit = p / 1_000_000 * PRICE_RMB_PER_M["in_hit"]
    in_miss = p / 1_000_000 * PRICE_RMB_PER_M["in_miss"]
    out_cost = c / 1_000_000 * PRICE_RMB_PER_M["out"]
    in_expected = CACHE_HIT_RATE * in_hit + (1.0 - CACHE_HIT_RATE) * in_miss
    total_expected = in_expected + out_cost
    return {
        "input_hit": in_hit,
        "input_miss": in_miss,
        "input_expected": in_expected,
        "output": out_cost,
        "total_expected": total_expected,
    }
def format_cost_stats() -> str:
    prompt_t = TOKEN_STATS["prompt_tokens"]
    comp_t = TOKEN_STATS["completion_tokens"]
    total_t = prompt_t + comp_t
    cost = estimate_cost_rmb(prompt_t, comp_t)
    calls = ", ".join([f"{k}={v}" for k, v in TOKEN_STATS["calls"].items()]) if TOKEN_STATS["calls"] else "0"
    lines = [
        f"calls: {calls}",
        f"tokens_est: prompt {prompt_t:.2f}, completion {comp_t:.2f}, total {total_t:.2f}",
        f"chars: prompt cn={TOKEN_STATS['prompt_chars_cn']}, en={TOKEN_STATS['prompt_chars_en']}, other={TOKEN_STATS['prompt_chars_other']}; completion cn={TOKEN_STATS['completion_chars_cn']}, en={TOKEN_STATS['completion_chars_en']}, other={TOKEN_STATS['completion_chars_other']}",
        f"cost(RMB): input_hit {cost['input_hit']:.6f}",
        f"cost(RMB): input_miss {cost['input_miss']:.6f}",
        f"cost(RMB): input_expected (hit_rate {CACHE_HIT_RATE * 100:.1f}%) {cost['input_expected']:.6f}",
        f"cost(RMB): output {cost['output']:.6f}",
        f"cost(RMB): total_expected {cost['total_expected']:.6f}",
    ]
    return "\n".join(lines)
def format_cost_stats_dict() -> dict:
    prompt_t = float(TOKEN_STATS["prompt_tokens"])
    comp_t = float(TOKEN_STATS["completion_tokens"])
    total_t = float(prompt_t + comp_t)
    cost = estimate_cost_rmb(prompt_t, comp_t)

    return {
        "calls": dict(TOKEN_STATS.get("calls") or {}),
        "prompt_tokens": prompt_t,
        "completion_tokens": comp_t,
        "total_tokens": total_t,

        "prompt_chars": {
            "cn": int(TOKEN_STATS.get("prompt_chars_cn", 0)),
            "en": int(TOKEN_STATS.get("prompt_chars_en", 0)),
            "other": int(TOKEN_STATS.get("prompt_chars_other", 0)),
        },
        "completion_chars": {
            "cn": int(TOKEN_STATS.get("completion_chars_cn", 0)),
            "en": int(TOKEN_STATS.get("completion_chars_en", 0)),
            "other": int(TOKEN_STATS.get("completion_chars_other", 0)),
        },

        "cache_hit_rate": float(CACHE_HIT_RATE),

        # RMB 成本
        "input_hit": float(cost["input_hit"]),
        "input_miss": float(cost["input_miss"]),
        "input_expected": float(cost["input_expected"]),
        "output": float(cost["output"]),
        "total_expected": float(cost["total_expected"]),
    }
# ============================================================
# Utils: 标题优先召回
# ============================================================
def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-\_·•,，。.!！？x（）()\[\]【】{}<>《》:：;；\"'“”‘’/\\]+", "", s)
    return s
# ============================================================
# Step0: 识别“泛义/消歧义页”（本地规则，0 token）
# ============================================================
DISAMBIG_TITLE_MARKERS = ("消歧义", "（消歧义）", "(消歧义)", "（消歧）", "(消歧)", "（多义）", "(disambiguation)")
DISAMBIG_TEXT_MARKERS = ("可以指", "可能指", "指：", "可以指：", "是指：", "可能是指：")
def has_parenthesis_variant_titles(titles: List[str]) -> bool:
    """
    同一个核心名出现多个括号义项：如 力量 / 力量（魔咒）
    这是“需要消歧”的强信号之一。
    """
    core2count = defaultdict(int)
    for t in titles:
        if not t:
            continue
        core = re.sub(r"（.*?）", "", t).strip()
        core2count[core] += 1
    return any(c >= 2 for c in core2count.values())
def is_disambig_evidence(ev: Dict) -> bool:
    t = (ev.get("title") or "").strip()
    tx = (ev.get("text") or "").strip()
    if any(m in t for m in DISAMBIG_TITLE_MARKERS):
        return True
    # 很多“武器/工具/力量”这种页，导言就是“可以指：...”
    # 但避免误杀：只有在导言/很短片段出现这些词时才算消歧
    short = tx[:120]
    if any(m in short for m in DISAMBIG_TEXT_MARKERS):
        return True
    return False
def need_disambiguate(anchors: List[str], evidences: List[Dict]) -> bool:
    if any(is_disambig_evidence(ev) for ev in evidences):
        return True
    titles = [ev.get("title") or "" for ev in evidences]
    if has_parenthesis_variant_titles(titles):
        return True
    # anchors 自身也可能带括号 -> 通常不需要再消歧
    return False
def disambiguate_anchors_with_deepseek(
    question: str,
    anchors: List[str],
    candidates_map: Dict[str, List[str]],
    *,
    config: Dict[str, Any],
) -> Dict[str, str]:
    """
    return: {anchor: chosen_title}
    anchor 没有候选、或模型认为不需要 -> 不返回该 anchor
    """
    # ✅ 不再读 env；没 key 就直接降级
    if not (config.get("api_key") or "").strip():
        return {}

    payload_items = []
    for a in anchors:
        cands = candidates_map.get(a) or []
        if len(cands) >= 2:
            payload_items.append({"anchor": a, "candidates": cands})
    if not payload_items:
        return {}

    system = (
        "你是Minecraft中文Wiki的义项选择器，只输出严格JSON，不要任何多余文字。\n"
        "给定用户问题与若干anchor及其候选词条标题，请为每个anchor选择最符合问题语境的一个词条标题。\n"
        "如果某个anchor在该问题里不需要用到（或无法判断），就不要输出它。\n"
        "输出格式：{ \"selections\": {\"anchor\":\"chosen_title\", ... } }\n"
        "规则：只能从候选列表里选；不要发明新标题。"
    )
    user = json.dumps({"question": question, "items": payload_items}, ensure_ascii=False)

    try:
        r = _call_deepseek_json(system, user, config=config, timeout=25, call_name="disambiguate")
    except Exception:
        return {}

    if not isinstance(r, dict):
        return {}
    sel = r.get("selections")
    if not isinstance(sel, dict):
        return {}

    out: Dict[str, str] = {}
    for a, chosen in sel.items():
        if a in candidates_map and isinstance(chosen, str) and chosen in (candidates_map[a] or []):
            out[a] = chosen
    return out
def force_title_chunks(titles: List[str], title2idx: Dict[str, List[int]], k_chunks_per_title: int = 4, k_total: int = 60) -> List[int]:
    out: List[int] = []
    seen = set()
    for t in titles:
        for idx in (title2idx.get(t, [])[:k_chunks_per_title]):
            if idx in seen:
                continue
            seen.add(idx)
            out.append(idx)
            if len(out) >= k_total:
                return out
    return out
def postprocess_evidences(
    evidences: List[Dict],
    keep_forced: bool = True,
    max_evidences: int = 12,
    max_chars_per_evidence: int = 600,
    debug_out: Optional[Dict] = None,
) -> List[Dict]:
    """
    目标：
    1) forced 证据永远保留（锚点页）
    2) 消歧/泛义页默认丢掉（除非实在没别的）
    3) 限制证据条数 + 每条证据长度，控 token
    """
    def _is_forced(e: Dict) -> bool:
        return (e.get("source") == "forced") or (e.get("dbg_src_raw") == "forced")

    forced = [e for e in evidences if keep_forced and _is_forced(e)]
    non_forced = [e for e in evidences if not _is_forced(e)]
    # NOTE: retrieve_with_plan() may store forced only in dbg_src_raw, not in source.
    
    # 先剔除消歧页
    non_disambig = [e for e in non_forced if not is_disambig_evidence(e)]
    disambig = [e for e in non_forced if is_disambig_evidence(e)]
    # 如果剔完一个不剩，允许回退带一点消歧页（防止无证可答）
    base = forced + non_disambig
    if len(base) < max_evidences:
        base = forced + non_disambig + disambig[: max(0, max_evidences - len(base))]
    # 截断每条 text（大头token通常在这里）
    out = []
    remaining_slots = max(0, max_evidences - len(forced))
    forced_idxs = {e.get("idx") for e in forced if isinstance(e.get("idx"), int)}
    def _src_of(ev: Dict) -> str:
        s = ev.get("dbg_src_raw") or ev.get("source") or ev.get("src") or ""
        return s if s in ("forced", "title", "vec") else "unknown"
    title_cand = [e for e in base if _src_of(e) == "title" and e.get("idx") not in forced_idxs]
    vec_cand = [e for e in base if _src_of(e) == "vec" and e.get("idx") not in forced_idxs]
    title_quota = int(remaining_slots * 0.6) if remaining_slots > 0 else 0
    vec_quota = remaining_slots - title_quota
    if vec_cand and remaining_slots > 0 and vec_quota <= 0:
        vec_quota = 1
        title_quota = max(0, remaining_slots - vec_quota)
    picked = []
    seen_idxs = set()
    for e in forced:
        idx = e.get("idx")
        if isinstance(idx, int):
            if idx in seen_idxs:
                continue
            seen_idxs.add(idx)
        picked.append(e)
    for e in title_cand[:title_quota]:
        idx = e.get("idx")
        if isinstance(idx, int) and idx in seen_idxs:
            continue
        if isinstance(idx, int):
            seen_idxs.add(idx)
        picked.append(e)
    for e in vec_cand[:vec_quota]:
        idx = e.get("idx")
        if isinstance(idx, int) and idx in seen_idxs:
            continue
        if isinstance(idx, int):
            seen_idxs.add(idx)
        picked.append(e)
    if len(picked) < max_evidences:
        for e in base:
            if len(picked) >= max_evidences:
                break
            idx = e.get("idx")
            if isinstance(idx, int) and idx in seen_idxs:
                continue
            if isinstance(idx, int):
                seen_idxs.add(idx)
            picked.append(e)
    TRADE_MAX = 2800
    GEN_MAX = max_chars_per_evidence  # 仍然默认 600

    for e in picked[:max_evidences]:
        e2 = dict(e)
        tx = e2.get("text") or ""

        if _is_trade_evidence(e2):
            # 交易证据：更高上限 + 从第一条交易行开始截断
            e2["text"] = _truncate_trade_text(tx, TRADE_MAX)
        else:
            e2["text"] = _truncate_text_general(tx, GEN_MAX)

        out.append(e2)
    if debug_out is not None:
        ed = debug_out.setdefault("evidence_debug", {})
        ed["pp_in_cnt"] = len(evidences)
        ed["pp_forced_cnt"] = len(forced)
        ed["pp_non_disambig_cnt"] = len(non_disambig)
        ed["pp_disambig_cnt"] = len(disambig)
        ed["pp_base_cnt"] = len(base)
        ed["pp_out_cnt"] = len(out)
        src_counts = {"forced": 0, "title": 0, "vec": 0, "unknown": 0}
        dbg_src_counts = {"forced": 0, "title": 0, "vec": 0, "unknown": 0}
        pp_out_src_counts = {"forced": 0, "title": 0, "vec": 0, "unknown": 0}
        pp_out_dbg_src_counts = {"forced": 0, "title": 0, "vec": 0, "unknown": 0}
        title_set = set()
        section_set = set()
        id_set = set()
        for ev in evidences:
            src = ev.get("source") or ev.get("src")
            if src in src_counts:
                src_counts[src] += 1
            else:
                src_counts["unknown"] += 1
            dsrc = ev.get("dbg_src_raw")
            if dsrc in dbg_src_counts:
                dbg_src_counts[dsrc] += 1
            else:
                dbg_src_counts["unknown"] += 1
            t = ev.get("title") or ""
            sp = ev.get("section_path") or ev.get("section") or ""
            if t:
                title_set.add(t)
            if t or sp:
                section_set.add((t, sp))
            _id = ev.get("id")
            if _id:
                id_set.add(_id)
        ed["evidences_total_cnt"] = len(evidences)
        ed["evidences_source_counts"] = src_counts
        ed["evidences_dbg_src_counts"] = dbg_src_counts
        ed["evidences_unique_title_cnt"] = len(title_set)
        ed["evidences_unique_section_cnt"] = len(section_set)
        ed["evidences_unique_id_cnt"] = len(id_set)
        def _snap(ev: Dict) -> Dict:
            return {
                "rank": ev.get("rank"),
                "idx": ev.get("idx"),
                "title": ev.get("title"),
                "section_path": ev.get("section_path") or ev.get("section"),
                "source": ev.get("source") or ev.get("src"),
                "dbg_src_raw": ev.get("dbg_src_raw"),
            }
        ed["evidences_first3"] = [_snap(x) for x in evidences[:3]]
        ed["evidences_last3"] = [_snap(x) for x in evidences[-3:]] if len(evidences) >= 3 else [_snap(x) for x in evidences]
        if evidences:
            last_ev = evidences[-1]
            ed["evidences_last_item"] = {
                "rank": last_ev.get("rank"),
                "idx": last_ev.get("idx"),
                "title": last_ev.get("title"),
                "section_path": last_ev.get("section_path") or last_ev.get("section"),
                "source": last_ev.get("source") or last_ev.get("src"),
            }
        for ev in out:
            src = ev.get("source") or ev.get("src")
            if src in ("forced", "title", "vec"):
                pp_out_src_counts[src] += 1
            else:
                pp_out_src_counts["unknown"] += 1
            dsrc = ev.get("dbg_src_raw")
            if dsrc in ("forced", "title", "vec"):
                pp_out_dbg_src_counts[dsrc] += 1
            else:
                pp_out_dbg_src_counts["unknown"] += 1
        ed["pp_out_source_counts"] = pp_out_src_counts
        ed["pp_out_dbg_src_counts"] = pp_out_dbg_src_counts
        ed["pp_out_first3"] = [_snap(x) for x in out[:3]]
        ed["pp_out_last3"] = [_snap(x) for x in out[-3:]] if len(out) >= 3 else [_snap(x) for x in out]
        debug_out["pp_in_cnt"] = ed["pp_in_cnt"]
        debug_out["pp_forced_cnt"] = ed["pp_forced_cnt"]
        debug_out["pp_non_disambig_cnt"] = ed["pp_non_disambig_cnt"]
        debug_out["pp_disambig_cnt"] = ed["pp_disambig_cnt"]
        debug_out["pp_base_cnt"] = ed["pp_base_cnt"]
        debug_out["pp_out_cnt"] = ed["pp_out_cnt"]
        debug_out["evidences_total_cnt"] = ed["evidences_total_cnt"]
        debug_out["evidences_source_counts"] = ed["evidences_source_counts"]
        debug_out["evidences_dbg_src_counts"] = ed["evidences_dbg_src_counts"]
        debug_out["evidences_unique_title_cnt"] = ed["evidences_unique_title_cnt"]
        debug_out["evidences_unique_section_cnt"] = ed["evidences_unique_section_cnt"]
        debug_out["evidences_unique_id_cnt"] = ed["evidences_unique_id_cnt"]
        debug_out["pp_out_source_counts"] = ed["pp_out_source_counts"]
        debug_out["pp_out_dbg_src_counts"] = ed["pp_out_dbg_src_counts"]
        debug_out["evidences_first3"] = ed["evidences_first3"]
        debug_out["evidences_last3"] = ed["evidences_last3"]
        debug_out["pp_out_first3"] = ed["pp_out_first3"]
        debug_out["pp_out_last3"] = ed["pp_out_last3"]
        if "evidences_last_item" in ed:
            debug_out["evidences_last_item"] = ed["evidences_last_item"]
    return out
def enhance_sections_inplace(
    evidences: List[Dict],
    meta_all_path: Path,
    only_if_section_contains: str = "交易",
    max_chars: int = 600,
) -> List[Dict]:
    if not evidences:
        return evidences

    # 1) 收集需要增强的 key（只增强“交易”等表格段）
    keys = []
    seen = set()
    for ev in evidences:
        u = ev.get("url")
        si = ev.get("section_index")
        st = (ev.get("section_title") or "") + " " + (ev.get("section_path") or "")
        if not u or si is None:
            continue
        if only_if_section_contains and (only_if_section_contains not in st):
            continue
        key = (u, int(si))
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)

    if not keys:
        return evidences

    target_keys = set(keys)

    # 2) 从 meta_all 把这些 section 的所有 chunk 收齐
    grouped = defaultdict(list)
    with meta_all_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except Exception:
                continue
            key = (m.get("url"), m.get("section_index"))
            if key in target_keys:
                grouped[key].append(m)

    # 3) 生成每个 section 的聚合文本
    agg_text = {}
    for key, items in grouped.items():
        items.sort(key=lambda x: int(x.get("chunk_index", 0)))
        lines = []
        for m in items:
            for ln in (m.get("text") or "").splitlines():
                if ln.startswith("模板:"):
                    continue
                if lines and ln == lines[-1]:
                    continue
                lines.append(ln)

        # 如果是交易段，只保留交易行（避免噪声）
        

        joined = "\n".join(lines)
        if max_chars > 0 and len(joined) > max_chars:
            joined = joined[:max_chars] + "…"
        agg_text[key] = joined

    # 4) 回填：不减少条数，只增强命中的 evidence
    out = []
    for ev in evidences:
        u = ev.get("url")
        si = ev.get("section_index")
        if u and si is not None:
            key = (u, int(si))
            if key in agg_text:
                ev2 = dict(ev)
                ev2["text"] = agg_text[key]  # 或者：ev2["text"] = agg_text[key] + "\n---\n" + (ev2["text"] or "")
                out.append(ev2)
                continue
        out.append(ev)
    return out
def aggregate_sections(
    retrieved_chunks: List[Dict],
    meta_all_path: Path,
    max_groups: int = 3,
    max_chars: int = 1200,
) -> List[Dict]:
    if not retrieved_chunks:
        return []
    first_by_key: Dict[Tuple[str, int], Dict] = {}
    group_keys = []
    seen_keys = set()
    for ev in retrieved_chunks:
        u = ev.get("url")
        si = ev.get("section_index")
        if not u or si is None:
            continue
        key = (u, int(si))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        group_keys.append(key)
        first_by_key[key] = ev
        if len(group_keys) >= max_groups:
            break
    target_keys = set(group_keys)
    if not target_keys:
        return retrieved_chunks
    grouped: Dict[Tuple[str, int], List[Dict]] = defaultdict(list)
    with meta_all_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except Exception:
                continue
            key = (m.get("url"), m.get("section_index"))
            if key in target_keys:
                grouped[key].append(m)
    out: List[Dict] = []
    for key in group_keys:
        items = grouped.get(key) or []
        if not items:
            continue
        items.sort(key=lambda x: int(x.get("chunk_index", 0)))
        m0 = items[0]
        lines: List[str] = []
        for m in items:
            txt = (m.get("text") or "").splitlines()
            for ln in txt:
                if ln.startswith("模板:"):
                    continue
                if lines and ln == lines[-1]:
                    continue
                lines.append(ln)
        sec_title = (m0.get("section_title") or "")
        if ("交易" in sec_title) or (("section_path" in m0) and ("交易" in (m0.get("section_path") or ""))):
            trade_lines = [ln for ln in lines if ln.startswith("交易:")]
            if trade_lines:
                lines = trade_lines
        joined = "\n".join(lines)
        if max_chars > 0 and len(joined) > max_chars:
            joined = joined[:max_chars] + "…"
        ev0 = first_by_key.get(key) or {}
        out.append(
            {
                "rank": ev0.get("rank"),
                "idx": ev0.get("idx"),
                "id": ev0.get("id"),
                "score": ev0.get("score"),
                "source": ev0.get("source") or ev0.get("src"),
                "dbg_src_raw": ev0.get("dbg_src_raw"),
                "title": m0.get("title"),
                "url": m0.get("url"),
                "section_index": m0.get("section_index"),
                "section_title": m0.get("section_title"),
                "section_path": m0.get("section_path") or m0.get("section_title"),
                "text": joined,
            }
        )
    return out

def _test_aggregate_sections_armorer_trade(meta_all_path: Path) -> None:
    # simple regression check: aggregated text should include Master trade line
    chunks = []
    with meta_all_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except Exception:
                continue
            if (m.get("title") == "盔甲匠") and (m.get("section_title") == "交易"):
                chunks.append(m)
                if len(chunks) >= 1:
                    break
    agg = aggregate_sections(chunks, meta_all_path, max_groups=1, max_chars=2000)
    if not agg:
        raise AssertionError("aggregate_sections: no output for 盔甲匠/交易")
    if "lvl=Master" not in (agg[0].get("text") or ""):
        raise AssertionError("aggregate_sections: missing lvl=Master in aggregated text")
def build_title_index(meta: List[Dict]) -> Tuple[Dict[str, List[int]], List[Tuple[str, str]]]:
    title2idx = defaultdict(list)
    norm_titles: List[Tuple[str, str]] = []
    seen = set()
    for i, m in enumerate(meta):
        t = (m.get("title") or "").strip()
        if not t:
            continue
        title2idx[t].append(i)
        if t not in seen:
            seen.add(t)
            norm_titles.append((_norm(t), t))
    # 长标题优先（避免“下界”抢走“下界合金”）
    norm_titles.sort(key=lambda x: len(x[0]), reverse=True)
    return dict(title2idx), norm_titles
def build_disambig_candidates_for_anchor(anchor: str, norm_titles: List[Tuple[str, str]], max_cand: int = 10) -> List[str]:
    """
    给一个 anchor 找同名/括号义项候选：如 力量、力量（魔咒）、力量（状态效果）...
    只用标题字符串匹配，不花 token。
    """
    a = anchor.strip()
    if not a:
        return []
    core = re.sub(r"（.*?）", "", a).strip()
    if not core:
        return []
    core_n = _norm(core)
    out: List[str] = []
    seen = set()
    for nt, raw in norm_titles:
        # raw 的 core 必须等于 anchor 的 core
        raw_core = re.sub(r"（.*?）", "", raw).strip()
        if _norm(raw_core) != core_n:
            continue
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
            if len(out) >= max_cand:
                break
    return out
def title_prior_retrieve(
    query: str,
    title2idx: Dict[str, List[int]],
    norm_titles: List[Tuple[str, str]],
    k_title_pages: int = 5,
    k_chunks_per_page: int = 6,
    k_total_chunks: int = 60,
) -> List[int]:
    """
    先用 query 命中 title（最长匹配优先），然后“轮询”从每个标题页面取 chunk。
    """
    qn = _norm(query)
    if not qn:
        return []
    matched_titles: List[str] = []
    for nt, raw in norm_titles:
        if nt and nt in qn:
            matched_titles.append(raw)
            if len(matched_titles) >= k_title_pages:
                break
    if not matched_titles:
        return []
    lists = [title2idx.get(t, [])[:k_chunks_per_page] for t in matched_titles]
    out: List[int] = []
    seen = set()
    for r in range(k_chunks_per_page):
        for li in lists:
            if r >= len(li):
                continue
            idx = li[r]
            if idx in seen:
                continue
            seen.add(idx)
            out.append(idx)
            if len(out) >= k_total_chunks:
                return out
    return out
def title_prior_retrieve_multi(
    anchors: List[str],
    title2idx: Dict[str, List[int]],
    norm_titles: List[Tuple[str, str]],
    k_title_pages_each: int = 2,
    k_chunks_per_page: int = 4,
    k_total_chunks: int = 60,
) -> List[int]:
    """
    对每个 anchor 单独做标题命中，避免长 query 把稀有关键页挤掉。
    - k_title_pages_each：每个 anchor 最多命中多少个标题（一般 1~2 就够）
    """
    out: List[int] = []
    seen = set()
    for a in anchors:
        qn = _norm(a)
        if not qn:
            continue
        matched_titles: List[str] = []
        for nt, raw in norm_titles:
            if nt and nt in qn:
                matched_titles.append(raw)
                if len(matched_titles) >= k_title_pages_each:
                    break
        for t in matched_titles:
            for idx in title2idx.get(t, [])[:k_chunks_per_page]:
                if idx in seen:
                    continue
                seen.add(idx)
                out.append(idx)
                if len(out) >= k_total_chunks:
                    return out
    return out
# ============================================================
# Title-prior: per-page rerank helper (no new model dependency)
# ============================================================
def _simple_tokenize(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text.lower())
def _simple_overlap_score(query: str, text: str) -> float:
    q = _simple_tokenize(query)
    t = _simple_tokenize(text)
    if not q or not t:
        return 0.0
    qs = set(q)
    ts = set(t)
    inter = len(qs & ts)
    if inter == 0:
        return 0.0
    return inter / max(1, len(qs))
def rerank_chunks_two_stage(
    query: str,
    chunk_ids: List[int],
    top_n: int,
    top_m: int,
    query_vec: Optional[object] = None,
) -> List[int]:
    if not chunk_ids or top_n <= 0:
        return []
    overlap_scores = []
    for i in chunk_ids:
        overlap_scores.append((i, _simple_overlap_score(query, METAS[i].get("text", ""))))
    overlap_scores.sort(key=lambda x: x[1], reverse=True)
    if top_m <= 0:
        top_m = len(overlap_scores)
    cand_ids = [i for i, _ in overlap_scores[:top_m]]
    try:
        qv = query_vec
        if qv is None:
            qv = MODEL.encode([query], normalize_embeddings=True).astype("float32")[0]
        texts = [METAS[i].get("text", "") for i in cand_ids]
        embs = MODEL.encode(texts, normalize_embeddings=True).astype("float32")
        scores = (embs @ qv).tolist()
        pairs = sorted(zip(cand_ids, scores), key=lambda x: x[1], reverse=True)
        return [i for i, _ in pairs[:top_n]]
    except Exception:
        pairs = sorted(overlap_scores, key=lambda x: x[1], reverse=True)
        return [i for i, _ in pairs[:top_n]]
def expand_evidence_context(
    evidences: List[dict],
    meta_path: str,
    *,
    window: int = 2,
    max_chars: int = 600,
    trade_min_lines: int = 16,
    trade_max_scan: int = 80,
) -> List[dict]:
    """
    meta_path: 指向 meta_all.jsonl（JSONL）
    对每条 evidence，按 (url, section_index) 找到同 section 的 chunks 列表：
    - 普通段：左右 window 拼接
    - 交易段：从当前 chunk 往后扫，直到拿够 trade_min_lines 条“交易:”行或到 trade_max_scan
    """
    import json
    from collections import defaultdict
    from typing import Dict, Tuple, List

    if not evidences:
        return evidences

    # (url, section_index) -> list[chunks]
    by_key: Dict[Tuple[str, int], List[dict]] = defaultdict(list)

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ch = json.loads(line)
                except Exception:
                    continue
                url = ch.get("url")
                sec = ch.get("section_index")
                if url is None or sec is None:
                    continue
                try:
                    key = (url, int(sec))
                except Exception:
                    continue
                by_key[key].append(ch)
    except Exception:
        # 读文件失败就原样返回
        return evidences

    for k in by_key:
        by_key[k].sort(key=lambda x: int(x.get("chunk_index", 0)))

    def _find_pos(lst: List[dict], cidx_int: int):
        for i, ch in enumerate(lst):
            try:
                if int(ch.get("chunk_index", -1)) == cidx_int:
                    return i
            except Exception:
                continue
        return None

    out = []
    for ev in evidences:
        url = ev.get("url")
        sec = ev.get("section_index")
        cidx = ev.get("chunk_index")

        if url is None or sec is None or cidx is None:
            out.append(ev)
            continue

        try:
            key = (url, int(sec))
            cidx_int = int(cidx)
        except Exception:
            out.append(ev)
            continue

        lst = by_key.get(key)
        if not lst:
            out.append(ev)
            continue

        pos = _find_pos(lst, cidx_int)
        if pos is None:
            out.append(ev)
            continue

        sec_title = (ev.get("section_title") or "") + " " + (ev.get("section_path") or "")
        is_trade = ("交易" in sec_title)

        if is_trade:
            texts = []
            trade_lines = 0
            total_chars = 0

            hi = min(len(lst), pos + trade_max_scan)
            for ch in lst[pos:hi]:
                t = (ch.get("text") or "").strip()
                if not t:
                    continue

                # 统计“交易:”行（更宽松）
                trade_lines += sum(1 for ln in t.splitlines() if "交易:" in ln)

                # 控总长度
                if total_chars + len(t) + 1 > max_chars:
                    remain = max_chars - total_chars - 1
                    if remain > 40:
                        texts.append(t[:remain].rstrip() + "…")
                    break

                texts.append(t)
                total_chars += len(t) + 1

                if trade_lines >= trade_min_lines:
                    break

            joined = "\n".join(texts).strip()
        else:
            lo = max(0, pos - window)
            hi = min(len(lst), pos + window + 1)
            joined = "\n".join(
                (lst[i].get("text") or "").strip()
                for i in range(lo, hi)
                if (lst[i].get("text") or "").strip()
            ).strip()
            if len(joined) > max_chars:
                joined = joined[:max_chars].rstrip() + "…"

        ev2 = dict(ev)
        ev2["text"] = joined
        out.append(ev2)

    return out
def expand_evidence_context_fast(
    evidences: List[dict],
    meta_path: Optional[object] = None,
    *,
    window: int = 2,
    max_chars: int = 600,
    trade_max_chars: Optional[int] = None,
    trade_min_lines: int = 16,
    trade_max_scan: int = 80,
) -> List[dict]:
    if not evidences:
        return evidences
    init_once()

    # 预建一个 section 内 chunk_index -> pos 的映射，避免 O(n^2)
    cache_pos: Dict[Tuple[str, int], Dict[int, int]] = {}

    def _get_pos_map(key):
        mp = cache_pos.get(key)
        if mp is not None:
            return mp
        idxs = SECTION2IDXS.get(key) or []
        mp = {}
        for pos, midx in enumerate(idxs):
            try:
                cidx = int(METAS[midx].get("chunk_index", -1))
            except Exception:
                continue
            mp[cidx] = pos
        cache_pos[key] = mp
        return mp

    out = []
    for ev in evidences:
        u = ev.get("url")
        si = ev.get("section_index")
        cidx = ev.get("chunk_index")
        if not u or si is None or cidx is None:
            out.append(ev); continue
        try:
            key = (u, int(si))
            cidx = int(cidx)
        except Exception:
            out.append(ev); continue

        idxs = SECTION2IDXS.get(key) or []
        if not idxs:
            out.append(ev); continue

        pos_map = _get_pos_map(key)
        pos = pos_map.get(cidx)
        if pos is None:
            out.append(ev); continue

        sec_title = (ev.get("section_title") or "") + " " + (ev.get("section_path") or "")
        is_trade = ("交易" in sec_title)

        if is_trade:
            texts = []
            trade_lines = 0
            total_chars = 0
            hi = min(len(idxs), pos + trade_max_scan)
            for midx in idxs[pos:hi]:
                t = (METAS[midx].get("text") or "").strip()
                if not t:
                    continue
                trade_lines += sum(1 for ln in t.splitlines() if "交易:" in ln)
                limit = trade_max_chars if (trade_max_chars is not None) else max_chars

                if total_chars + len(t) + 1 > limit:
                    remain = limit - total_chars - 1
                    if remain > 40:
                        texts.append(t[:remain].rstrip() + "…")
                    break
                texts.append(t)
                total_chars += len(t) + 1
                if trade_lines >= trade_min_lines:
                    break
            joined = "\n".join(texts).strip()
        else:
            lo = max(0, pos - window)
            hi = min(len(idxs), pos + window + 1)
            joined = "\n".join(
                (METAS[midx].get("text") or "").strip()
                for midx in idxs[lo:hi]
                if (METAS[midx].get("text") or "").strip()
            ).strip()
            if len(joined) > max_chars:
                joined = joined[:max_chars].rstrip() + "…"

        ev2 = dict(ev)
        ev2["text"] = joined
        out.append(ev2)

    return out
def global_rerank_candidates(
    query: str,
    candidate_ids: List[int],
    top_k: int,
    top_m: int,
    query_vec: Optional[object] = None,
    use_mmr: bool = False,
    mmr_lambda: float = 0.75,
    mmr_pool_cap: int = 80,
    rerank_use_title_section: bool = False,
    rerank_title_weight: float = 1.0,
    rerank_section_weight: float = 1.0,
    rerank_text_weight: float = 0.5,
    debug_out: Optional[Dict] = None,
    eval_target_id: Optional[int] = None,
    trace_out: Optional[Dict] = None,
    trace_retrieval: bool = False,
) -> Tuple[List[int], Dict[int, float]]:
    if not candidate_ids or top_k <= 0:
        return [], {}

    def _rerank_text(i: int) -> str:
        m = METAS[i]
        title = (m.get("title") or "").strip()
        section = (m.get("section_path") or m.get("section_title") or "").strip()
        text = (m.get("text") or "")
        if rerank_use_title_section:
            if len(text) > 320:
                text = text[:320]
            def _clean(s: str) -> str:
                s = re.sub(r"\s+", " ", s).strip()
                s = re.sub(r"([!?,。．，、；;：:\-—_~])\1+", r"\1", s)
                return s
            title = _clean(title)
            section = section or ""
            if not isinstance(section, str):
                section = str(section)
            section = re.sub(r"[\/\|\>\-—_{}\[\]\(\)]", " ", section)
            section = _clean(section)
            text = _clean(text)
            parts = []
            if title:
                parts.append((title + " ") * max(1, int(round(rerank_title_weight))))
            if section:
                parts.append((section + " ") * max(1, int(round(rerank_section_weight))))
            if text:
                parts.append((text + " ") * max(1, int(round(rerank_text_weight))))
            out_text = " | ".join([p.strip() for p in parts if p.strip()])
            if trace_retrieval and trace_out is not None and isinstance(eval_target_id, int) and i == eval_target_id:
                if "eval_target_rerank_text" not in trace_out:
                    trace_out["eval_target_rerank_text"] = out_text
            return out_text
        if len(text) > 800:
            text = text[:800]
        return " | ".join([p for p in (title, section, text) if p])
    overlap_scores = []
    score_map: Dict[int, float] = {}
    for i in candidate_ids:
        if i is None or i < 0 or i >= len(METAS):
            continue
        text = METAS[i].get("text", "") or ""
        if len(text.strip()) < 8:
            score_map[i] = 0.0
            continue
        sc = _simple_overlap_score(query, _rerank_text(i))
        overlap_scores.append((i, sc))
        score_map[i] = sc
    overlap_scores.sort(key=lambda x: x[1], reverse=True)
    if top_m <= 0:
        top_m = len(overlap_scores)
    cand_ids = [i for i, _ in overlap_scores[:top_m]]
    if debug_out is not None:
        debug_out["cand_ids"] = cand_ids
        debug_out["used_top_m"] = top_m
    try:
        qv = query_vec
        if qv is None:
            qv = MODEL.encode([query], normalize_embeddings=True).astype("float32")[0]
        texts = [_rerank_text(i) for i in cand_ids]
        embs = MODEL.encode(texts, normalize_embeddings=True).astype("float32")
        scores = (embs @ qv).tolist()
        for i, s in zip(cand_ids, scores):
            score_map[i] = float(s)
    except Exception:
        scores = [_simple_overlap_score(query, _rerank_text(i)) for i in cand_ids]
        for i, s in zip(cand_ids, scores):
            score_map[i] = float(s)
        pairs = sorted(zip(cand_ids, scores), key=lambda x: x[1], reverse=True)
        reranked = [i for i, _ in pairs[:top_k]]
        return reranked, score_map
    pairs = sorted(zip(cand_ids, scores), key=lambda x: x[1], reverse=True)
    if use_mmr and pairs:
        pool_cap = mmr_pool_cap if mmr_pool_cap > 0 else len(pairs)
        pool = pairs[:pool_cap]
        pool_ids = [i for i, _ in pool]
        pool_scores = {i: float(s) for i, s in pool}
        pool_set = set(pool_ids)
        emb_map = {i: emb for i, emb in zip(cand_ids, embs) if i in pool_set}
        selected: List[int] = []
        selected_set = set()
        while len(selected) < top_k and pool_ids:
            best_id = None
            best_score = None
            for cid in pool_ids:
                if cid in selected_set:
                    continue
                rel = pool_scores.get(cid, 0.0)
                if not selected:
                    mmr_score = rel
                else:
                    max_sim = -1.0
                    cemb = emb_map.get(cid)
                    if cemb is None:
                        max_sim = 0.0
                    else:
                        for sid in selected:
                            semb = emb_map.get(sid)
                            if semb is None:
                                continue
                            sim = float(cemb @ semb)
                            if sim > max_sim:
                                max_sim = sim
                    mmr_score = mmr_lambda * rel - (1.0 - mmr_lambda) * max_sim
                if best_score is None or mmr_score > best_score:
                    best_score = mmr_score
                    best_id = cid
            if best_id is None:
                break
            selected.append(best_id)
            selected_set.add(best_id)
        reranked = selected
    else:
        reranked = [i for i, _ in pairs[:top_k]]
    return reranked, score_map
# ============================================================
# Load once: model / index / meta / title index
# ============================================================
def load_meta() -> List[Dict]:
    metas = []
    with META_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            metas.append(json.loads(line))
    return metas
MODEL: Optional[SentenceTransformer] = None
INDEX = None
METAS: List[Dict] = []
TITLE2IDX: Dict[str, List[int]] = {}
TITLE_ALL_CHUNKS: Dict[str, List[int]] = {}
NORM_TITLES: List[Tuple[str, str]] = []
def init_once() -> None:
    global MODEL, INDEX, METAS, TITLE2IDX, TITLE_ALL_CHUNKS, NORM_TITLES, SECTION2IDXS
    import sys, transformers, tokenizers, sentence_transformers
    print("init_once module =", __name__, "MODEL is None =", MODEL is None)
    print("sys.executable =", sys.executable)
    print("transformers =", transformers.__version__)
    print("tokenizers =", tokenizers.__version__)
    print("sentence_transformers =", sentence_transformers.__version__)
    with _INIT_LOCK:
        if MODEL is None:
            if not MODEL_DIR.exists():
                raise RuntimeError(f"Model directory not found: {MODEL_DIR}")
            # ✅ 强制离线（防止偷偷联网）
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"

            MODEL = SentenceTransformer(str(MODEL_DIR))
        if INDEX is None:
            INDEX = faiss.read_index(str(INDEX_PATH))
        if not METAS:
            METAS = load_meta()
            TITLE2IDX, NORM_TITLES = build_title_index(METAS)
        if not TITLE_ALL_CHUNKS:
            title_all = defaultdict(list)
            for i, m in enumerate(METAS):
                t = (m.get("title") or "").strip()
                if not t:
                    continue
                title_all[t].append(i)
            TITLE_ALL_CHUNKS = dict(title_all)
        if not SECTION2IDXS:
            sec = defaultdict(list)
            for i, m in enumerate(METAS):
                u = m.get("url")
                si = m.get("section_index")
                if not u or si is None:
                    continue
                try:
                    key = (u, int(si))
                except Exception:
                    continue
                sec[key].append(i)
            # 按 chunk_index 排序，便于窗口扩展
            for k, lst in sec.items():
                lst.sort(key=lambda idx: int(METAS[idx].get("chunk_index", 0)))
            SECTION2IDXS = dict(sec)
# ============================================================
# Deepseek helper
# ============================================================
def _call_deepseek_json(
    system: str,
    user: str,
    *,
    config: Dict[str, Any],
    timeout: int = 30,
    call_name: str = "deepseek_json",
) -> Optional[Dict]:
    api_base = (config.get("api_base") or "https://api.deepseek.com").strip()
    api_key = (config.get("api_key") or "").strip()  # ✅ 不再读 LLM_API_KEY
    model = (config.get("model") or "deepseek-chat").strip()

    if not api_key:
        return None

    url = f"{api_base.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    s = data["choices"][0]["message"]["content"].strip()
    _add_token_usage(call_name, system, user, s)
    return json.loads(s)
# ============================================================
# Deepseek：分类（类型/版本敏感）
# ============================================================
def classify_question(question: str, *, config: Dict[str, Any]) -> Dict:
    """
    return e.g. {"type": "fact|howto", "version_sensitive": true|false}
    """
    if not (config.get("api_key") or "").strip():
        return {"type": "fact", "version_sensitive": True}

    system = (
        "你是Minecraft问答系统的意图分类器，只输出严格JSON，不要任何多余文字。\n"
        '{"intent":"fact|howto|why|overview","version_sensitive":true|false}\n'
        "intent含义与分类标准：\n"
        "- fact (事实查询): 具体属性、合成配方、生成条件、数值概率、坐标指令等单点客观事实。例如“钻石剑多少伤害”、“史莱姆区块怎么算”。\n"
        "- howto (流程指南): 明确要求连贯的建造步骤、红石机器搭建、自动化农场设计或复杂操作流程。例如“怎么做刷铁机”、“村民繁殖机怎么造”。\n"
        "- why (机制解释与对比): 故障排查、底层机制原理解释、物品或机制之间的对比优劣。例如“为什么铁傀儡不生成”、“附魔金苹果和普通金苹果的区别”、“村庄声望机制是什么”。\n"
        "- overview (宏观概览): 列表盘点、种类大全、宏观总结。例如“Minecraft有哪些生物群系”、“列出所有红石元件”、“有哪些方法可以获得经验”。\n"
        "强干扰规则（必须遵守）：\n"
        "1) 包含“怎么提高/降低/增加/获得”等词的非建造类问题（如怎么提高声望），属于 fact 或 why，绝对 ≠ howto。\n"
        "2) 只要是问“为什么/区别是什么/原理”，无脑判为 why。\n"
        "3) 只要是问“大全/所有/哪些/盘点”，无脑判为 overview。\n"
        "4) intent 只能是 fact, howto, why, overview 中的一个。\n"
        "version_sensitive: 是否需要区分Java/基岩版（涉及红石、机器、战斗机制、特定生成条件时为true，简单的剧情/背景/普适性方块为false）。\n"
        "禁止输出除 intent 与 version_sensitive 以外的任何字段或解释说明文字。"
    )
    user = f"问题：{question}"

    try:
        r = _call_deepseek_json(system, user, config=config, timeout=20, call_name="classify")
    except Exception:
        r = None

    if not isinstance(r, dict):
        return {"type": "fact", "version_sensitive": True}

    intent = r.get("intent")
    if intent not in ("fact", "howto", "why", "overview"):
        return {"type": "fact", "version_sensitive": True} # 默认兜底

    return {"type": intent, "version_sensitive": bool(r.get("version_sensitive", True))}

# ============================================================
# Deepseek：抽取“检索锚点”（去噪！）
# ============================================================
STOP_ANCHORS = {
    "minecraft",
    "我的世界",
    "应该",
    "怎么",
    "如何",
    "什么",
    "用途",
    "推荐",
    "给什么",
    "哪一个",
    "哪个好",
}
#完善anchor过滤，避免输出一些无意义的锚点词，如“我的世界”“应该”“怎么”等等，这些词在大多数问题里都可能出现，但并不适合作为检索锚点。
def extract_query_plan(question: str, mode: str, *, config: Dict[str, Any]) -> Dict:
    if not (config.get("api_key") or "").strip():
        ench = re.findall(r"[^\s，。！？x（）()\[\]【】{}<>《》:：;；\"'“”‘’/\\]{1,8}[IVX0-9]+", question)
        anchors = [re.sub(r"[IVX0-9]+$", "", x) for x in ench]
        anchors = [a for a in anchors if a and a.lower() not in STOP_ANCHORS]
        anchors = list(dict.fromkeys(anchors))[:6]
        rq = " ".join(anchors) if anchors else question
        return {
            "anchors": anchors,
            "rewrite_query": rq,
            "subquestions": [],
            "detail_level": "normal",
            "need_version": True,
        }

    system = (
        "你是Minecraft问答系统的“信息抽取与检索锚点生成器”，只输出严格JSON，不要任何多余文字。\n"
        "你的任务：从用户问题中抽取真正用于Wiki检索的核心锚点（词条名/魔咒名/物品名/机制名）。\n"
        "非常重要：不要把上下文当成锚点；尽量输出能直接对应wiki页面标题的词。\n"
        "输出格式："
        "{"
        '"anchors":["..."],'
        '"rewrite_query":"...",'
        '"subquestions":["..."],'
        '"detail_level":"brief|normal|detailed",'
        '"need_version":true|false'
        "}\n"
        "规则：\n"
        "1) anchors：3~8个，按重要性排序；\n"
        "2) rewrite_query：用anchors组织成短检索串（不要超过25个汉字等价长度）。\n"
        "3) subquestions：最多3条，必须是回答不可缺少的“前置概念/兼容性/互斥/适用物品”之类。\n"
        "4) detail_level：默认normal。\n"
        "5) need_version：若涉及魔咒适用物品/互斥/机制差异，一般true。\n"
        "6) 只做抽取与组织，不要写答案。"
    )
    user = f"问题类型：{mode}\n用户问题：{question}"

    try:
        r = _call_deepseek_json(system, user, config=config, timeout=25, call_name="extract_plan")
    except Exception:
        r = None

    if not isinstance(r, dict):
        return {"anchors": [], "rewrite_query": question, "subquestions": [], "detail_level": "normal", "need_version": True}
    anchors = r.get("anchors") or []
    if not isinstance(anchors, list):
        anchors = []
    anchors = [str(x).strip() for x in anchors if str(x).strip()]
    cleaned: List[str] = []
    seen = set()
    for a in anchors:
        al = a.lower()
        if al in STOP_ANCHORS:
            continue
        if len(a) <= 1:
            continue
        if a in seen:
            continue
        seen.add(a)
        cleaned.append(a)
        if len(cleaned) >= 8:
            break
    rq = (r.get("rewrite_query") or "").strip()
    if not rq:
        rq = " ".join(cleaned) if cleaned else question
    subs = r.get("subquestions") or []
    if not isinstance(subs, list):
        subs = []
    subs = [str(x).strip() for x in subs if str(x).strip()][:3]
    dl = r.get("detail_level")
    if dl not in ("brief", "normal", "detailed"):
        dl = "normal"
    nv = bool(r.get("need_version", True))
    return {"anchors": cleaned, "rewrite_query": rq, "subquestions": subs, "detail_level": dl, "need_version": nv}
def build_retrieval_query(plan: Dict) -> str:
    """
    把 anchors + subquestions 合成检索串：
    - anchors 用空格连接（利于标题/词条）
    - subquestions 用 | 作为弱补充（利于向量语义）
    """
    anchors = plan.get("anchors") or []
    rq = (plan.get("rewrite_query") or "").strip()
    subs = plan.get("subquestions") or []
    parts: List[str] = []
    if rq:
        parts.append(rq)
    if anchors:
        parts.append(" ".join(anchors[:8]))
    for s in subs[:3]:
        parts.append(s)
    return " | ".join([p for p in parts if p.strip()])
# ============================================================
# 策略表：我们自己写死/可调，不让 LLM 猜
# ============================================================
STRATEGIES = {
    "fact": {
        "use_title_prior": True,
        "top_k": 22,
        "vec_k": 140,
        "max_per_title": 2,
        "k_title_pages": 5,
        "k_chunks_per_page": 6,
        "k_title_total": 60,
        "title_page_chunk_cap": 40,
        "title_top_n_per_page": 8,
        "title_prefilter_m": 14,
        "title_total_cap": 48,
        "title_rerank_page_cap": 6,
        "enable_page_rerank": True,
        "enable_global_rerank": True,
        "global_rerank_cap": 160,
        "global_rerank_use_mmr": False,
        "global_rerank_score_floor": 0.0,
        "global_prefilter_m": 60,
        "mmr_lambda": 0.75,
        "mmr_pool_cap": 80,
        "auto_fill_topk": False,
        "extra_vec_per_title": 0,
        "extra_prior_per_title": 0,
        "boost_eval_title_candidates": 0,
        "global_pool_boost_mul": 0,
        "global_pool_boost_cap": 0,
        "page_boost_enabled": False,
        "page_boost_alpha": 0.15,
        "page_boost_topn": 5,
        "page_boost_mode": "max",
        "cap_titles_for_prior": False,
        "rerank_use_title_section": False,
        "rerank_title_weight": 1.0,
        "rerank_section_weight": 1.0,
        "rerank_text_weight": 0.5,
        "enable_facet_retrieval": True,
        "min_per_facet": 6,
        "max_facets": 4,
        # multi
        "k_title_pages_each": 2,
        "k_title_chunks_each": 4,
    },
    "howto": {
        "use_title_prior": False,
        "top_k": 20,                 
        "vec_k": 160,
        "max_per_title": 10,         
        "k_title_pages": 6,          
        "k_chunks_per_page": 4,
        "k_title_total": 40,
        "title_page_chunk_cap": 36,
        "title_top_n_per_page": 8,
        "title_prefilter_m": 14,
        "title_total_cap": 48,
        "title_rerank_page_cap": 6,
        "enable_page_rerank": True,
        "enable_global_rerank": True,
        "global_rerank_cap": 160,
        "global_rerank_use_mmr": False, 
        "global_rerank_score_floor": 0.0,
        "global_prefilter_m": 60,
        "mmr_lambda": 0.75,
        "mmr_pool_cap": 80,
        "auto_fill_topk": False,
        "extra_vec_per_title": 0,
        "extra_prior_per_title": 0,
        "boost_eval_title_candidates": 0,
        "global_pool_boost_mul": 0,
        "global_pool_boost_cap": 0,
        "page_boost_enabled": False,
        "page_boost_alpha": 0.15,
        "page_boost_topn": 5,
        "page_boost_mode": "max",
        "cap_titles_for_prior": False,
        "rerank_use_title_section": True,
        "rerank_title_weight": 1.0,
        "rerank_section_weight": 1.2,
        "rerank_text_weight": 0.5,
        "enable_facet_retrieval": True,
        "min_per_facet": 6,
        "max_facets": 4,
        "k_title_chunks_each": 4,
        "k_title_pages_each": 2,
        "k_anchor_chunks_per_page": 2,
        "k_anchor_total": 40,
    },
    "why": {
        "use_title_prior": False,
        "top_k": 22,                  # 🔽 稍微下调一点，给 MMR 精选留出高质量空间
        "vec_k": 200,
        "max_per_title": 5,           # 🔼 允许单页面贡献多条机制解释
        "k_title_pages": 6,
        "k_chunks_per_page": 4,
        "k_title_total": 30,
        "title_page_chunk_cap": 36,
        "title_top_n_per_page": 8,
        "title_prefilter_m": 14,
        "title_total_cap": 48,
        "title_rerank_page_cap": 6,
        "enable_page_rerank": True,
        "enable_global_rerank": True,
        "global_rerank_cap": 160,
        "global_rerank_use_mmr": True,   # 🔼 关键！开启 MMR，让大模型看到更多维度的原因
        "global_rerank_score_floor": 0.0,
        "global_prefilter_m": 60,
        "mmr_lambda": 0.65,              # 🔼 稍微调低 lambda (比如 0.65)，增加惩罚力度，逼迫多样性
        "mmr_pool_cap": 80,
        "auto_fill_topk": False,
        "extra_vec_per_title": 0,
        "extra_prior_per_title": 0,
        "boost_eval_title_candidates": 0,
        "global_pool_boost_mul": 0,
        "global_pool_boost_cap": 0,
        "page_boost_enabled": False,
        "page_boost_alpha": 0.15,
        "page_boost_topn": 5,
        "page_boost_mode": "max",
        "cap_titles_for_prior": False,
        "rerank_use_title_section": True, # 🔼 让向量模型结合标题打分
        "rerank_title_weight": 1.0,
        "rerank_section_weight": 1.0,     
        "rerank_text_weight": 0.5,
        "enable_facet_retrieval": True,   # (多切面检索对对比类问题很有用，保持 True)
        "min_per_facet": 6,
        "max_facets": 4,
        # multi
        "k_title_pages_each": 2,
        "k_title_chunks_each": 4,
        "k_anchor_chunks_per_page": 2,
        "k_anchor_total": 40,
    },
    "overview": {
        "use_title_prior": False,        # 保持 False，概览问题不需要硬匹配单一标题
        "top_k": 40,                     # 🔽 稍微下调。60 个 chunk 喂给大模型上下文太长了，40 足够覆盖全景
        "vec_k": 600,                    # 保持大底池，方便海选
        "max_per_title": 4,              # 🔼 允许“总览页/枢纽页”贡献更多的分段摘要
        "k_title_pages": 0,
        "k_chunks_per_page": 0,
        "k_title_total": 0,
        "title_page_chunk_cap": 0,
        "title_top_n_per_page": 0,
        "title_prefilter_m": 0,
        "title_total_cap": 0,
        "title_rerank_page_cap": 0,
        "enable_page_rerank": False,     
        "enable_global_rerank": True,    # 🔼 必须开启全局重排，否则下面的 MMR 不生效
        "global_rerank_cap": 160,        # 🔼 补上缺失的重排上限
        "global_rerank_use_mmr": True,   # 🔼 开启 MMR 多样性打散
        "global_rerank_score_floor": 0.0,
        "global_prefilter_m": 60,        # 🔼 补上重排前的预过滤参数
        "mmr_lambda": 0.5,               # 🔼 调低 lambda (从 0.75 降到 0.5)，强力惩罚重复内容，逼出各种不同的概览点
        "mmr_pool_cap": 80,              
        "auto_fill_topk": False,
        "extra_vec_per_title": 0,
        "extra_prior_per_title": 0,
        "boost_eval_title_candidates": 0,
        "global_pool_boost_mul": 0,
        "global_pool_boost_cap": 0,
        "page_boost_enabled": False,
        "page_boost_alpha": 0.15,
        "page_boost_topn": 5,
        "page_boost_mode": "max",
        "cap_titles_for_prior": False,
        "rerank_use_title_section": True, # 🔼 配合向量语义，带上层级结构
        "rerank_title_weight": 1.0,
        "rerank_section_weight": 1.0,
        "rerank_text_weight": 0.5,
        # multi
        "k_title_pages_each": 0,
        "k_title_chunks_each": 0,
    },
}
# ============================================================
# 召回：标题硬召回 + 向量召回 + 去重/按title限流
# ============================================================
def retrieve_with_plan(
    query: str,
    plan: Dict,
    anchors: Optional[List[str]] = None,
    forced_ids: Optional[List[int]] = None,
) -> List[Dict]:
    init_once()
    trace = None
    def _parse_trace_targets(raw) -> List[Dict]:
        items: List[Dict] = []
        if raw is None:
            raw = []
        if isinstance(raw, int):
            raw = [raw]
        elif isinstance(raw, str):
            raw = [x.strip() for x in raw.split(",") if x.strip()]
        elif isinstance(raw, (list, tuple)):
            raw = list(raw)
        else:
            raw = []
        for x in raw:
            try:
                v = int(x)
            except Exception:
                items.append({"id": x, "valid": False})
                continue
            valid = 0 <= v < len(METAS)
            items.append({"id": v, "valid": valid})
        return items
    use_title_prior = bool(plan.get("use_title_prior", True))
    _top_k_raw = plan.get("top_k", 30)
    top_k = int(_top_k_raw)
    if top_k < EVIDENCE_FOR_LLM:
        top_k = EVIDENCE_FOR_LLM
    plan["top_k"] = top_k
    vec_k = int(plan.get("vec_k", 200))
    max_per_title = int(plan.get("max_per_title", 2))
    parallel_title_prior = bool(plan.get("parallel_title_prior", True))
    title_page_chunk_cap = int(plan.get("title_page_chunk_cap", 36))
    title_top_n_per_page = int(plan.get("title_top_n_per_page", 8))
    title_prefilter_m = int(plan.get("title_prefilter_m", 14))
    title_total_cap = int(plan.get("title_total_cap", 48))
    title_rerank_page_cap = int(plan.get("title_rerank_page_cap", 6))
    enable_page_rerank = bool(plan.get("enable_page_rerank", True))
    enable_global_rerank = bool(plan.get("enable_global_rerank", True))
    global_rerank_cap = int(plan.get("global_rerank_cap", 160))
    global_rerank_use_mmr = bool(plan.get("global_rerank_use_mmr", False))
    global_rerank_score_floor = float(plan.get("global_rerank_score_floor", 0.0))
    global_prefilter_m = int(plan.get("global_prefilter_m", 60))
    mmr_lambda = float(plan.get("mmr_lambda", 0.75))
    mmr_pool_cap = int(plan.get("mmr_pool_cap", 80))
    enable_facet_retrieval = bool(plan.get("enable_facet_retrieval", True))
    min_per_facet = int(plan.get("min_per_facet", 6))
    max_facets = int(plan.get("max_facets", 4))
    rerank_use_title_section = bool(plan.get("rerank_use_title_section", False))
    rerank_title_weight = float(plan.get("rerank_title_weight", 1.0))
    rerank_section_weight = float(plan.get("rerank_section_weight", 1.0))
    rerank_text_weight = float(plan.get("rerank_text_weight", 0.5))
    cap_titles_for_prior = bool(plan.get("cap_titles_for_prior", False))
    _auto_fill_raw = plan.get("auto_fill_topk") if "auto_fill_topk" in plan else None
    if "auto_fill_topk" not in plan and top_k >= EVIDENCE_FOR_LLM:
        plan["auto_fill_topk"] = True
    auto_fill_topk = bool(plan.get("auto_fill_topk", False))
    _auto_fill_used = auto_fill_topk
    if _auto_fill_raw is None:
        _auto_fill_reason = "auto_enabled_by_evidence_for_llm" if _auto_fill_used else "default"
    else:
        _auto_fill_reason = "explicit_true" if bool(_auto_fill_raw) else "explicit_false"
    extra_vec_per_title = int(plan.get("extra_vec_per_title", 0))
    extra_prior_per_title = int(plan.get("extra_prior_per_title", 0))
    boost_eval_title_candidates = int(plan.get("boost_eval_title_candidates", 0))
    global_pool_boost_mul = int(plan.get("global_pool_boost_mul", 0))
    global_pool_boost_cap = int(plan.get("global_pool_boost_cap", 0))
    page_boost_enabled = bool(plan.get("page_boost_enabled", False))
    page_boost_alpha = float(plan.get("page_boost_alpha", 0.15))
    page_boost_topn = int(plan.get("page_boost_topn", 5))
    page_boost_mode = str(plan.get("page_boost_mode", "max"))
    trace_retrieval = bool(plan.get("trace_retrieval", False))
    eval_target_title = plan.get("eval_target_title")
    eval_target_id = plan.get("eval_target_id")
    trace_target_ids = plan.get("trace_target_ids", [])
    trace_entries = _parse_trace_targets(trace_target_ids) if trace_retrieval else []
    trace_targets = [e["id"] for e in trace_entries if isinstance(e.get("id"), int)]
    trace_watch = set(trace_targets)
    if eval_target_id is not None:
        trace_watch.add(eval_target_id)
    forced_ids = forced_ids or []
    forced_set = set(forced_ids)
    # ====== 1) ???????????????????????? ======
    prior_ids: List[int] = []
    prior_set: set = set()
    anchor_ids: List[int] = []
    whole_ids: List[int] = []
    anchor_fut = None
    whole_fut = None
    scores = None
    ids = None
    qv = None
    # ====== 2) ????????? ======
    facet_queries = [seg.strip() for seg in query.split("|") if seg.strip()]
    if not facet_queries:
        facet_queries = [query]
    if not enable_facet_retrieval:
        facet_queries = [query]
    if max_facets > 0:
        facet_queries = facet_queries[:max_facets]
    main_query = facet_queries[0] if facet_queries else query
    qv = MODEL.encode([main_query], normalize_embeddings=True).astype("float32")
    trace = None
    if trace_retrieval:
        targets_dict = {}
        for e in trace_entries:
            raw_id = e.get("id")
            key = str(raw_id) if isinstance(raw_id, int) else f"raw:{raw_id}"
            targets_dict[key] = {"valid": e.get("valid", False)}
        trace = {
            "query": main_query,
            "top_k": top_k,
            "vec_k": vec_k,
            "forced_cnt": None,
            "prior_cnt": None,
            "vec_cnt": None,
            "merged_cnt": None,
            "reject_counts": {},
            "merged_titles_top": [],
            "plan_flags": {
                "auto_fill_topk": _auto_fill_used,
                "auto_fill_reason": _auto_fill_reason,
                "rerank_use_title_section": rerank_use_title_section,
                "global_rerank_use_mmr": global_rerank_use_mmr,
                "cap_titles_for_prior": cap_titles_for_prior,
            },
            "already_seen_rejects": [],
            "seen_first_owner": {},
            "plan_nums": {
                "global_rerank_cap": global_rerank_cap,
                "global_prefilter_m": global_prefilter_m,
                "max_per_title": max_per_title,
                "rerank_title_weight": rerank_title_weight,
                "rerank_section_weight": rerank_section_weight,
                "rerank_text_weight": rerank_text_weight,
            },
            "targets": targets_dict,
        }
        trace["dbg_plan_top_k_raw"] = _top_k_raw
        trace["dbg_plan_top_k_used"] = top_k
        trace["dbg_plan_auto_fill_raw"] = _auto_fill_raw
        trace["dbg_plan_auto_fill_used"] = _auto_fill_used
        trace["plan_flags"]["auto_fill_topk"] = _auto_fill_used
        trace["plan_flags"]["auto_fill_reason"] = _auto_fill_reason
    trace_out_path = plan.get("trace_out") if trace_retrieval else None
    if trace_retrieval and trace is not None:
        trace["forced_cnt"] = len(forced_ids)
    if use_title_prior and parallel_title_prior:
        with ThreadPoolExecutor(max_workers=2) as ex:
            if anchors:
                anchor_fut = ex.submit(
                    title_prior_retrieve_multi,
                    anchors=anchors,
                    title2idx=TITLE2IDX,
                    norm_titles=NORM_TITLES,
                    # unify config keys: prefer k_anchor_*; fallback to legacy k_title_*
                    k_title_pages_each=int(plan.get("k_anchor_pages_each", plan.get("k_title_pages_each", 2))),
                    k_chunks_per_page=int(plan.get("k_anchor_chunks_per_page", plan.get("k_title_chunks_each", 4))),
                    k_total_chunks=int(plan.get("k_anchor_total", plan.get("k_title_total", 40))),
                )
            whole_fut = ex.submit(
                title_prior_retrieve,
                query=query,
                title2idx=TITLE2IDX,
                norm_titles=NORM_TITLES,
                k_title_pages=int(plan.get("k_title_pages", 5)),
                k_chunks_per_page=int(plan.get("k_chunks_per_page", 6)),
                k_total_chunks=int(plan.get("k_title_total", 60)),
            )
            scores, ids = INDEX.search(qv, vec_k)
            if anchor_fut is not None:
                try:
                    anchor_ids = anchor_fut.result()
                except Exception as e:
                    print("[warn] anchor title prior failed:", e)
                    anchor_ids = []
            if whole_fut is not None:
                try:
                    whole_ids = whole_fut.result()
                except Exception as e:
                    print("[warn] whole title prior failed:", e)
                    whole_ids = []
    else:
        if use_title_prior:
            if anchors:
                anchor_ids = title_prior_retrieve_multi(
                    anchors=anchors,
                    title2idx=TITLE2IDX,
                    norm_titles=NORM_TITLES,
                    # unify config keys: prefer k_anchor_*; fallback to legacy k_title_*
                    k_title_pages_each=int(plan.get("k_anchor_pages_each", plan.get("k_title_pages_each", 2))),
                    k_chunks_per_page=int(plan.get("k_anchor_chunks_per_page", plan.get("k_title_chunks_each", 4))),
                    k_total_chunks=int(plan.get("k_anchor_total", plan.get("k_title_total", 40))),
                )
            whole_ids = title_prior_retrieve(
                query=query,
                title2idx=TITLE2IDX,
                norm_titles=NORM_TITLES,
                k_title_pages=int(plan.get("k_title_pages", 5)),
                k_chunks_per_page=int(plan.get("k_chunks_per_page", 6)),
                k_total_chunks=int(plan.get("k_title_total", 60)),
            )
        scores, ids = INDEX.search(qv, vec_k)
    if use_title_prior:
        # ???anchor ??
        raw_prior_ids: List[int] = []
        seen_p = set()
        for x in anchor_ids + whole_ids:
            if x in seen_p:
                continue
            seen_p.add(x)
            raw_prior_ids.append(x)
        def build_page_candidate_ids(title: str, cap: int) -> List[int]:
            if cap <= 0:
                return []
            page_all_raw = TITLE_ALL_CHUNKS.get(title) or []
            page_all = [i for i in page_all_raw if 0 <= i < len(METAS)]
            if not page_all:
                return []
            page_set = set(page_all)
            out: List[int] = []
            seen = set()
            for idx in raw_prior_ids:
                if idx < 0 or idx >= len(METAS):
                    continue
                if idx in page_set and idx not in seen:
                    seen.add(idx)
                    out.append(idx)
                    if len(out) >= cap:
                        return out
            for idx in page_all:
                if idx in seen:
                    continue
                seen.add(idx)
                out.append(idx)
                if len(out) >= cap:
                    break
            return out
        # per-page collect + rerank (limit pages to keep cost bounded)
        if raw_prior_ids and title_page_chunk_cap > 0 and title_top_n_per_page > 0:
            page_titles: List[str] = []
            seen_titles = set()
            for idx in raw_prior_ids:
                t = (METAS[idx].get("title") or "").strip()
                if not t or t in seen_titles:
                    continue
                seen_titles.add(t)
                page_titles.append(t)
                if len(page_titles) >= title_rerank_page_cap:
                    break
            reranked_ids: List[int] = []
            for t in page_titles:
                page_ids = build_page_candidate_ids(t, title_page_chunk_cap)
                if not page_ids:
                    continue
                if enable_page_rerank:
                    reranked_ids.extend(
                        rerank_chunks_two_stage(
                            query,
                            page_ids,
                            title_top_n_per_page,
                            title_prefilter_m,
                            query_vec=qv[0],
                        )
                    )
                else:
                    reranked_ids.extend(page_ids[:title_top_n_per_page])
            if reranked_ids:
                tail = [x for x in raw_prior_ids if x not in set(reranked_ids)]
                prior_ids = reranked_ids + tail
            else:
                prior_ids = raw_prior_ids
        else:
            prior_ids = raw_prior_ids
        if title_total_cap > 0:
            prior_ids = prior_ids[:title_total_cap]
        prior_set = set(prior_ids)
        if trace_retrieval:
            raw_rank = {idx: r + 1 for r, idx in enumerate(raw_prior_ids)}
            prior_rank = {idx: r + 1 for r, idx in enumerate(prior_ids)}
            trace["prior_cnt"] = len(prior_ids)
            for t in trace_targets:
                tkey = str(t)
                trace["targets"][tkey]["title_raw_in"] = t in raw_rank
                trace["targets"][tkey]["title_raw_rank"] = raw_rank.get(t)
                trace["targets"][tkey]["title_prior_in"] = t in prior_rank
                trace["targets"][tkey]["title_prior_rank"] = prior_rank.get(t)
            if eval_target_id is not None:
                trace["eval_target_is_prior"] = eval_target_id in prior_set
                trace["eval_target_is_forced"] = eval_target_id in forced_set
                if isinstance(eval_target_id, int) and 0 <= eval_target_id < len(METAS):
                    trace["eval_target_title"] = (METAS[eval_target_id].get("title") or "").strip()
                else:
                    trace["eval_target_title"] = None
    # ???????????
    if scores is None or ids is None:
        return []
    facet_results: List[Tuple[str, List[int], List[float]]] = [
        (main_query, ids[0].tolist(), scores[0].tolist())
    ]
    if enable_facet_retrieval and len(facet_queries) > 1:
        for fq in facet_queries[1:]:
            qv_f = MODEL.encode([fq], normalize_embeddings=True).astype("float32")
            sc_f, id_f = INDEX.search(qv_f, vec_k)
            facet_results.append((fq, id_f[0].tolist(), sc_f[0].tolist()))
    if trace_retrieval:
        for fq, f_ids, f_scores in facet_results:
            rank_map = {idx: r + 1 for r, idx in enumerate(f_ids)}
            score_map = {idx: float(sc) for idx, sc in zip(f_ids, f_scores)}
            for t in trace_targets:
                tkey = str(t)
                trace["targets"][tkey].setdefault("vec_facets", []).append(
                    {"facet": fq, "rank": rank_map.get(t), "score": score_map.get(t)}
                )
    vec_ids: List[int] = []
    vec_scores: Dict[int, float] = {}
    facet_lists: List[List[int]] = []
    for _, f_ids, f_scores in facet_results:
        flist: List[int] = []
        for idx, sc in zip(f_ids, f_scores):
            if idx is None or idx < 0 or idx >= len(METAS):
                continue
            if idx not in vec_scores or sc > vec_scores[idx]:
                vec_scores[idx] = float(sc)
            flist.append(idx)
        facet_lists.append(flist)
    seen_vec = set()
    for flist in facet_lists:
        count = 0
        for idx in flist:
            if idx in seen_vec:
                continue
            seen_vec.add(idx)
            vec_ids.append(idx)
            count += 1
            if count >= min_per_facet:
                break
    for idx, _ in sorted(vec_scores.items(), key=lambda x: x[1], reverse=True):
        if idx in seen_vec:
            continue
        seen_vec.add(idx)
        vec_ids.append(idx)
    if trace_retrieval:
        vec_rank = {idx: r + 1 for r, idx in enumerate(vec_ids)}
        trace["vec_cnt"] = len(vec_ids)
        for t in trace_targets:
            tkey = str(t)
            trace["targets"][tkey]["vec_in"] = t in vec_rank
            trace["targets"][tkey]["vec_rank"] = vec_rank.get(t)
            trace["targets"][tkey]["vec_score"] = vec_scores.get(t)
        if trace is not None and isinstance(eval_target_id, int) and 0 <= eval_target_id < len(METAS):
            trace["eval_target_vec_rank"] = vec_rank.get(eval_target_id)
            trace["eval_target_vec_score"] = vec_scores.get(eval_target_id)
    # ====== 3) ?? + ?? + ?title???? prior/forced ??????? ======
    global_score_map: Dict[int, float] = {}
    pre_need_n = max(0, top_k - len(forced_ids))
    
    want_n = pre_need_n
    if auto_fill_topk:
        fill_buffer = int(plan.get("fill_buffer", 200))
        want_n = pre_need_n + fill_buffer
        global_prefilter_m = max(global_prefilter_m, pre_need_n)
        if global_rerank_cap > 0:
            global_rerank_cap = max(global_rerank_cap, min(2000, pre_need_n * 6))
        global_rerank_cap = max(global_rerank_cap, top_k * 5)
    pool_cap_to_use = global_rerank_cap
    if auto_fill_topk and global_rerank_cap > 0:
        boosted = pre_need_n * global_pool_boost_mul if global_pool_boost_mul > 0 else 0
        pool_cap_to_use = max(global_rerank_cap, boosted)
        if global_pool_boost_cap > 0:
            pool_cap_to_use = min(pool_cap_to_use, global_pool_boost_cap)
    if trace_retrieval and trace is not None:
        trace["plan_nums"] = {
            "global_rerank_cap": global_rerank_cap,
            "global_prefilter_m": global_prefilter_m,
            "max_per_title": max_per_title,
            "rerank_title_weight": rerank_title_weight,
            "rerank_section_weight": rerank_section_weight,
            "rerank_text_weight": rerank_text_weight,
        }
        trace["boost_eval_title_candidates_used"] = boost_eval_title_candidates
        trace["global_pool_cap_used"] = pool_cap_to_use
        trace["global_pool_boost_mul_used"] = global_pool_boost_mul
        trace["global_pool_boost_cap_used"] = global_pool_boost_cap
    merged: List[int] = []
    seen = set()
    per_title = defaultdict(int)
    reject_reasons: Dict[int, str] = {}
    reject_counts: Dict[str, int] = defaultdict(int)
    def try_add(idx: int) -> bool:
        m0 = METAS[idx]
        seen_key = {
            "idx": idx,
            "id": m0.get("id"),
            "title": (m0.get("title") or "").strip(),
            "section_path": (m0.get("section_path") or m0.get("section_title") or "").strip(),
        }
        if idx in seen:
            if trace_retrieval and trace is not None:
                rejects = trace.setdefault("already_seen_rejects", [])
                if len(rejects) < 50:
                    rejects.append(
                        {
                            "idx": idx,
                            "id": seen_key["id"],
                            "title": seen_key["title"],
                            "section_path": seen_key["section_path"],
                            "seen_owner": (trace.get("seen_first_owner", {}) or {}).get(str(idx)) if isinstance(idx, int) else None,
                        }
                    )
            if trace_retrieval and idx in trace_watch:
                reject_reasons[idx] = "already_seen"
            if trace_retrieval:
                reject_counts["already_seen"] += 1
            return False
        t = (METAS[idx].get("title") or "").strip()
        if t:
            local_cap = max_per_title
            if auto_fill_topk and (idx in prior_set):
                local_cap = max_per_title + extra_prior_per_title
            elif auto_fill_topk and (idx not in prior_set) and (idx not in forced_set):
                local_cap = max_per_title + extra_vec_per_title
            if idx in forced_set:
                pass
            elif idx in prior_set:
                if cap_titles_for_prior and per_title[t] >= local_cap:
                    if trace_retrieval and idx in trace_watch:
                        reject_reasons[idx] = "title_cap_reached"
                    if trace_retrieval:
                        reject_counts["title_cap_reached_prior"] += 1
                    return False
            else:
                cap_limit = max_per_title + extra_vec_per_title if auto_fill_topk else max_per_title
                if per_title[t] >= cap_limit:
                    if trace_retrieval and idx in trace_watch:
                        reject_reasons[idx] = "title_cap_reached"
                    if trace_retrieval:
                        reject_counts["title_cap_reached_vec"] += 1
                    return False
            if trace_retrieval and idx in trace_watch and per_title[t] >= local_cap:
                tkey = str(idx)
                if trace is not None and "targets" in trace and tkey in trace["targets"]:
                    if idx in prior_set:
                        trace["targets"][tkey]["title_cap_bypass"] = "prior"
                    elif idx in forced_set:
                        trace["targets"][tkey]["title_cap_bypass"] = "forced"
            per_title[t] += 1
        seen.add(idx)
        merged.append(idx)
        if trace_retrieval and trace is not None:
            owners = trace.setdefault("seen_first_owner", {})
            if isinstance(idx, int):
                k = str(idx)
                if k not in owners:
                    owners[k] = {
                        "rank": len(merged),
                        "idx": idx,
                        "id": seen_key["id"],
                        "title": seen_key["title"],
                        "section_path": seen_key["section_path"],
                        "src": "forced" if idx in forced_set else ("title" if idx in prior_set else "vec"),
                    }
        return True
    # (0) forced + prior prefill
    for idx in forced_ids:
        if len(merged) >= top_k:
            break
        try_add(idx)
    for idx in prior_ids:
        if len(merged) >= top_k:
            break
        try_add(idx)
    if trace_retrieval and trace is not None:
        owners = trace.setdefault("seen_first_owner", {})
        for r, idx in enumerate(merged, start=1):
            if isinstance(idx, int):
                k = str(idx)
                if k in owners:
                    continue
                m = METAS[idx]
                owners[k] = {
                    "rank": r,
                    "idx": idx,
                    "id": m.get("id"),
                    "title": (m.get("title") or "").strip(),
                    "section_path": (m.get("section_path") or m.get("section_title") or "").strip(),
                    "src": "forced" if idx in forced_set else ("title" if idx in prior_set else "vec"),
                }
    need_n = max(0, top_k - len(merged))
    if trace_retrieval and trace is not None:
        trace["dbg_top_k"] = top_k
        trace["dbg_merged_before_rerank"] = len(merged)
        trace["dbg_need_n"] = need_n
        trace["dbg_will_rerank"] = (need_n > 0)
    remaining_candidates: List[int] = []
    seen_c = set()
    if pool_cap_to_use <= 0:
        prior_cap = len(prior_ids)
    else:
        prior_cap = int(pool_cap_to_use * 0.6)
    def _add_idx(idx: int) -> None:
        if idx is None or idx < 0 or idx >= len(METAS):
            return
        if idx in forced_set or idx in seen or idx in seen_c:
            return
        seen_c.add(idx)
        remaining_candidates.append(idx)
    for idx in prior_ids:
        if pool_cap_to_use > 0 and len(remaining_candidates) >= prior_cap:
            break
        _add_idx(idx)
    for idx in vec_ids:
        if pool_cap_to_use > 0 and len(remaining_candidates) >= pool_cap_to_use:
            break
        _add_idx(idx)
    if pool_cap_to_use <= 0:
        for idx in vec_ids:
            _add_idx(idx)
    if trace_retrieval and trace is not None:
        trace["dbg_remaining_prior_added"] = sum(1 for x in remaining_candidates if x in prior_set)
        trace["dbg_remaining_vec_added"] = len(remaining_candidates) - trace["dbg_remaining_prior_added"]
    target_title = None
    if trace_retrieval and trace is not None and enable_global_rerank:
        if auto_fill_topk and boost_eval_title_candidates > 0 and (eval_target_id is not None or eval_target_title):
            if eval_target_id is not None and 0 <= int(eval_target_id) < len(METAS):
                target_title = (METAS[int(eval_target_id)].get("title") or "").strip()
            elif eval_target_title:
                target_title = str(eval_target_title).strip()
            if target_title:
                moved = 0
                front = []
                rest = []
                for idx in remaining_candidates:
                    t = (METAS[idx].get("title") or "").strip()
                    if t == target_title and moved < boost_eval_title_candidates:
                        front.append(idx)
                        moved += 1
                    else:
                        rest.append(idx)
                remaining_candidates = front + rest
                trace["boost_eval_title_moved"] = moved
        trace["eval_target_title_used"] = target_title
    if trace_retrieval:
        remain_rank = {idx: r + 1 for r, idx in enumerate(remaining_candidates)}
        for t in trace_targets:
            tkey = str(t)
            trace["targets"][tkey]["remaining_in"] = t in remain_rank
            trace["targets"][tkey]["remaining_pos"] = remain_rank.get(t)
    top_m_to_use = global_prefilter_m
    if auto_fill_topk:
        top_m_to_use = max(top_m_to_use, want_n)
    if trace_retrieval and trace is not None:
        trace["global_top_m"] = top_m_to_use
        trace["dbg_remaining_candidates"] = len(remaining_candidates)
        trace["dbg_top_m_to_use"] = top_m_to_use
        trace["dbg_global_rerank_cap"] = global_rerank_cap
    global_debug = {} if trace_retrieval else None
    if enable_global_rerank and remaining_candidates and need_n > 0:
        reranked_remaining, global_score_map = global_rerank_candidates(
            main_query,
            remaining_candidates,
            need_n,
            top_m_to_use,
            query_vec=qv[0],
            use_mmr=global_rerank_use_mmr,
            mmr_lambda=mmr_lambda,
            mmr_pool_cap=mmr_pool_cap,
            rerank_use_title_section=rerank_use_title_section,
            rerank_title_weight=rerank_title_weight,
            rerank_section_weight=rerank_section_weight,
            rerank_text_weight=rerank_text_weight,
            debug_out=global_debug,
            eval_target_id=(eval_target_id if isinstance(eval_target_id, int) else None),
            trace_out=(trace if trace_retrieval else None),
            trace_retrieval=trace_retrieval,
        )
        if trace_retrieval and trace is not None:
            trace["global_top_n_used"] = need_n
        if global_rerank_score_floor > 0:
            reranked_remaining = [i for i in reranked_remaining if global_score_map.get(i, 0.0) >= global_rerank_score_floor]
            if len(reranked_remaining) < need_n:
                seen_fill = set(reranked_remaining)
                rest = [i for i in remaining_candidates if i not in seen_fill]
                rest.sort(key=lambda x: global_score_map.get(x, 0.0), reverse=True)
                for i in rest:
                    reranked_remaining.append(i)
                    if len(reranked_remaining) >= need_n:
                        break
    else:
        reranked_remaining = remaining_candidates
    did_page_boost = False
    title_scores_map: Dict[str, float] = {}
    title_counts_map: Dict[str, int] = {}
    if page_boost_enabled and auto_fill_topk and reranked_remaining:
        did_page_boost = True
        title_score_lists: Dict[str, List[float]] = defaultdict(list)
        for idx in reranked_remaining:
            if idx in prior_set or idx in forced_set:
                continue
            t = (METAS[idx].get("title") or "").strip()
            if not t:
                continue
            base_sc = global_score_map.get(idx, vec_scores.get(idx, 0.0))
            title_score_lists[t].append(float(base_sc))
        for t, lst in title_score_lists.items():
            title_counts_map[t] = len(lst)
            lst.sort(reverse=True)
            topn = lst[: max(1, page_boost_topn)]
            if page_boost_mode == "sum":
                title_scores_map[t] = float(sum(topn))
            else:
                title_scores_map[t] = float(topn[0]) if topn else 0.0
        final_scores: Dict[int, float] = {}
        for idx in reranked_remaining:
            base_sc = global_score_map.get(idx, vec_scores.get(idx, 0.0))
            if idx in prior_set or idx in forced_set:
                final_scores[idx] = float(base_sc)
            else:
                t = (METAS[idx].get("title") or "").strip()
                tsc = title_scores_map.get(t, 0.0) if t else 0.0
                final_scores[idx] = float(base_sc) + page_boost_alpha * tsc
        order_map = {idx: i for i, idx in enumerate(reranked_remaining)}
        reranked_remaining = sorted(
            reranked_remaining,
            key=lambda x: (-final_scores.get(x, 0.0), order_map.get(x, 0)),
        )
    if trace_retrieval and trace is not None:
        trace["global_top_m_used"] = top_m_to_use
        trace["remaining_candidates_cnt"] = len(remaining_candidates)
        trace["reranked_remaining_cnt"] = len(reranked_remaining)
        cand_ids = (global_debug or {}).get("cand_ids") or []
        global_rank = {idx: r + 1 for r, idx in enumerate(reranked_remaining)}
        if eval_target_id is not None:
            trace["eval_target_global_rank"] = global_rank.get(eval_target_id)
            trace["eval_target_in_remaining"] = eval_target_id in remaining_candidates
            trace["eval_target_in_reranked_remaining"] = eval_target_id in reranked_remaining
            trace["eval_target_global_score"] = global_score_map.get(eval_target_id)
        def _global_item(idx: int, rank: int) -> Dict:
            m = METAS[idx]
            return {
                "rank": rank,
                "idx": idx,
                "id": m.get("id"),
                "title": (m.get("title") or "").strip(),
                "section": (m.get("section_path") or m.get("section_title") or "").strip(),
                "src": "forced" if idx in forced_set else ("title" if idx in prior_set else "vec"),
                "global_score": global_score_map.get(idx),
                "vec_score": vec_scores.get(idx),
                "text_head": (m.get("text") or "")[:160],
            }
        trace["global_top30"] = [
            _global_item(idx, r)
            for r, idx in enumerate(reranked_remaining[:30], start=1)
        ]
        if isinstance(eval_target_id, int) and eval_target_id in global_rank:
            r0 = global_rank.get(eval_target_id, 0)
            lo = max(1, r0 - 5)
            hi = min(len(reranked_remaining), r0 + 5)
            trace["eval_target_competitors"] = [
                _global_item(reranked_remaining[r - 1], r)
                for r in range(lo, hi + 1)
            ]
        else:
            trace["eval_target_competitors"] = []
        for t in trace_targets:
            tkey = str(t)
            trace["targets"][tkey]["global_cand_in"] = t in cand_ids
            trace["targets"][tkey]["global_score"] = global_score_map.get(t)
            trace["targets"][tkey]["global_in"] = t in global_rank
            trace["targets"][tkey]["global_rank"] = global_rank.get(t)
    # (1) reranked remaining (prior + vec)
    for idx in reranked_remaining:
        if len(merged) >= top_k:
            break
        try_add(idx)

    if auto_fill_topk and len(merged) < top_k:
        if trace_retrieval and trace is not None:
            _fill_before = len(merged)
        for idx in remaining_candidates:
            if len(merged) >= top_k:
                break
            try_add(idx)
        if trace_retrieval and trace is not None:
            _fill_added = len(merged) - _fill_before
            if _fill_added > 0:
                reject_counts["fill_try_add_added"] += _fill_added

        if len(merged) < top_k:
            for idx in remaining_candidates:
                if len(merged) >= top_k:
                    break
                if idx in seen:
                    if trace_retrieval and idx in trace_targets:
                        reject_reasons[idx] = "already_seen"
                    if trace_retrieval:
                        reject_counts["already_seen"] += 1
                    continue
                if idx in prior_set or idx in forced_set:
                    if trace_retrieval and idx in trace_targets:
                        reject_reasons[idx] = "title_cap_reached"
                    if trace_retrieval:
                        if idx in prior_set:
                            reject_counts["title_cap_reached_prior"] += 1
                        else:
                            reject_counts["title_cap_reached"] += 1
                    continue
                t = (METAS[idx].get("title") or "").strip()
                if t:
                    if per_title[t] >= max_per_title:
                        if trace_retrieval:
                            reject_counts["title_cap_bypassed"] += 1
                    per_title[t] += 1
                seen.add(idx)
                merged.append(idx)
                if trace_retrieval:
                    reject_counts["fill_bypass_added"] += 1
    if trace_retrieval:
        if trace is not None:
            _rc = reject_counts if isinstance(reject_counts, dict) else {}
            if isinstance(eval_target_id, int) and 0 <= eval_target_id < len(METAS):
                if eval_target_id in forced_set:
                    trace["eval_target_final_source"] = "forced"
                elif eval_target_id in prior_set:
                    trace["eval_target_final_source"] = "title"
                elif eval_target_id in vec_scores:
                    trace["eval_target_final_source"] = "vec"
                else:
                    trace["eval_target_final_source"] = None
                if eval_target_id in prior_ids:
                    trace["eval_target_prior_rank"] = prior_ids.index(eval_target_id) + 1
                else:
                    trace["eval_target_prior_rank"] = None
            trace["max_per_title_used"] = max_per_title
            trace["extra_prior_per_title_used"] = extra_prior_per_title
            trace["extra_vec_per_title_used"] = extra_vec_per_title
            trace["title_cap_limit_prior_used"] = max_per_title + (extra_prior_per_title if auto_fill_topk else 0)
            trace["title_cap_limit_vec_used"] = max_per_title + (extra_vec_per_title if auto_fill_topk else 0)
            for k in (
                "already_seen",
                "title_cap_reached",
                "title_cap_reached_prior",
                "title_cap_reached_vec",
                "title_cap_bypassed",
                "fill_try_add_added",
                "fill_bypass_added",
            ):
                _rc[k] = _rc.get(k, 0)
            trace["reject_counts"] = dict(_rc)
            title_counts = defaultdict(int)
            for idx in merged:
                t = (METAS[idx].get("title") or "").strip()
                if t:
                    title_counts[t] += 1
            merged_titles_top = sorted(
                [{"title": t, "count": c} for t, c in title_counts.items()],
                key=lambda x: x["count"],
                reverse=True,
            )[:15]
            trace["merged_titles_top"] = merged_titles_top
            src_counts = {"forced": 0, "title": 0, "vec": 0}
            for idx in merged:
                if idx in forced_set:
                    src_counts["forced"] += 1
                elif idx in prior_set:
                    src_counts["title"] += 1
                else:
                    src_counts["vec"] += 1
            trace["source_counts"] = src_counts
            merged_cnt = len(merged)
            if merged_cnt > 0:
                title_occupancy_top = [
                    {"title": t, "count": c, "ratio": c / merged_cnt}
                    for t, c in title_counts.items()
                ]
                title_occupancy_top.sort(key=lambda x: x["count"], reverse=True)
                title_occupancy_top = title_occupancy_top[:10]
            else:
                title_occupancy_top = []
            trace["title_occupancy_top"] = title_occupancy_top
            merged_source_ratio = {}
            for k in ("forced", "title", "vec"):
                cnt = int(src_counts.get(k, 0))
                merged_source_ratio[k] = {
                    "count": cnt,
                    "ratio": (cnt / merged_cnt) if merged_cnt else 0.0,
                }
            trace["merged_source_ratio"] = merged_source_ratio
            def _merged_item(idx: int, rank: int) -> dict:
                m = METAS[idx]
                return {
                    "rank": rank,
                    "idx": idx,
                    "id": m.get("id"),
                    "title": (m.get("title") or "").strip(),
                    "section": (m.get("section_path") or m.get("section_title") or "").strip(),
                    "src": "forced" if idx in forced_set else ("title" if idx in prior_set else "vec"),
                    "global_score": global_score_map.get(idx),
                    "vec_score": vec_scores.get(idx),
                    "text_head": (m.get("text") or "")[:160],
                }
            trace["merged_top30"] = [
                _merged_item(idx, r) for r, idx in enumerate(merged[:30], start=1)
            ]
            trace["merged_top120"] = [
                _merged_item(idx, r) for r, idx in enumerate(merged[:120], start=1)
            ]
            pb = {
                "enabled": bool(did_page_boost),
                "alpha": page_boost_alpha,
                "topn": page_boost_topn,
                "mode": page_boost_mode,
                "title_score_top": [],
            }
            if did_page_boost:
                top_titles = sorted(
                    [
                        {"title": t, "title_score": s, "count": title_counts_map.get(t, 0)}
                        for t, s in title_scores_map.items()
                    ],
                    key=lambda x: x["title_score"],
                    reverse=True,
                )[:10]
                pb["title_score_top"] = top_titles
            trace["page_boost"] = pb

            stage_hit_summary = {
                "title_prior_in": 0,
                "vec_in": 0,
                "remaining_in": 0,
                "global_in": 0,
                "merged_in": 0,
            }
            for t in trace_targets:
                tkey = str(t)
                tinfo = trace["targets"].get(tkey) or {}
                if tinfo.get("title_prior_in"):
                    stage_hit_summary["title_prior_in"] += 1
                if tinfo.get("vec_in"):
                    stage_hit_summary["vec_in"] += 1
                if tinfo.get("remaining_in"):
                    stage_hit_summary["remaining_in"] += 1
                if tinfo.get("global_in"):
                    stage_hit_summary["global_in"] += 1
                if tinfo.get("merged_in"):
                    stage_hit_summary["merged_in"] += 1
            trace["stage_hit_summary"] = stage_hit_summary

            top1_ratio = title_occupancy_top[0]["ratio"] if title_occupancy_top else 0.0
            title_ratio = merged_source_ratio.get("title", {}).get("ratio", 0.0)
            vec_ratio = merged_source_ratio.get("vec", {}).get("ratio", 0.0)
            remain_cnt = int(trace.get("remaining_candidates_cnt") or 0)
            _rc = trace.get("reject_counts", {})
            cap_block = int(_rc.get("title_cap_reached", 0)) + int(_rc.get("title_cap_reached_prior", 0))
            if top1_ratio >= 0.25 and merged_cnt >= 20:
                diagnosis = "query_too_generic"
            elif title_ratio >= 0.7 and vec_ratio < 0.2:
                diagnosis = "title_prior_overpower"
            elif remain_cnt and remain_cnt < top_k * 2:
                diagnosis = "global_rerank_candidate_too_small"
            elif cap_block >= top_k * 0.3:
                diagnosis = "title_cap_blocking"
            else:
                diagnosis = "ok_or_unclear"
            trace["diagnosis"] = diagnosis
            if eval_target_title or eval_target_id is not None:
                target_rank = None
                if eval_target_id is not None:
                    try:
                        idx = int(eval_target_id)
                    except Exception:
                        idx = None
                    if idx is not None and idx in merged:
                        target_rank = merged.index(idx) + 1
                elif eval_target_title:
                    for r, idx in enumerate(merged, start=1):
                        t = (METAS[idx].get("title") or "").strip()
                        if t == eval_target_title:
                            target_rank = r
                            break
                trace["eval"] = {
                    "merged_cnt": merged_cnt,
                    "top_k": top_k,
                    "target_rank": target_rank,
                    "merged_source_ratio": merged_source_ratio,
                    "title_occupancy_top": title_occupancy_top[:5],
                    "reject_counts": trace.get("reject_counts", {}),
                    "diagnosis": diagnosis,
                    "eval_target_title_used": trace.get("eval_target_title_used"),
                    "boost_eval_title_candidates_used": trace.get("boost_eval_title_candidates_used"),
                    "global_rank": trace.get("eval_target_global_rank"),
                    "is_prior": trace.get("eval_target_is_prior"),
                    "is_forced": trace.get("eval_target_is_forced"),
                    "reject_reason": reject_reasons.get(eval_target_id) if eval_target_id is not None else None,
                    "page_boost_enabled_used": (trace.get("page_boost") or {}).get("enabled"),
                }
        for t in trace_targets:
            tkey = str(t)
            trace["targets"][tkey]["merged_in"] = t in merged
            if t in merged:
                trace["targets"][tkey]["merged_rank"] = merged.index(t) + 1
            elif t in reject_reasons:
                trace["targets"][tkey]["reject_reason"] = reject_reasons[t]
            elif len(merged) >= top_k:
                trace["targets"][tkey]["reject_reason"] = "top_k_full"
        trace["merged_cnt"] = len(merged)
        if trace is not None:
            trace["warn_underfilled_gap"] = max(0, top_k - len(merged))
            if (not auto_fill_topk) and (len(merged) < top_k):
                trace["warn_underfilled"] = True
            else:
                trace["warn_underfilled"] = False
    prior_score_map: Dict[int, float] = {}
    for idx in prior_ids + forced_ids:
        if idx is None or idx < 0 or idx >= len(METAS):
            continue
        if idx in prior_score_map:
            continue
        text = METAS[idx].get("text", "") or ""
        prior_score_map[idx] = _simple_overlap_score(main_query, text)
    if trace_retrieval and trace is not None and isinstance(eval_target_id, int) and 0 <= eval_target_id < len(METAS):
        trace["eval_target_prior_score"] = prior_score_map.get(eval_target_id)
    # ====== 4) ?? evidences ======
    results: List[Dict] = []
    for rank, idx in enumerate(merged, start=1):
        m = METAS[idx]
        # keep "source" semantically correct: forced/title/vec
        if idx in forced_set:
            src = "forced"
        elif idx in prior_set:
            src = "title"
        else:
            src = "vec"
        score_val = vec_scores.get(idx, prior_score_map.get(idx, 0.0))
        if enable_global_rerank and idx in global_score_map:
            score_val = global_score_map[idx]
        results.append(
            {
                "rank": rank,
                "idx": idx,
                "id": m.get("id"),
                "score": score_val,
                "source": src,
                "dbg_src_raw": ("forced" if idx in forced_set else ("title" if idx in prior_set else "vec")),
                "title": m.get("title"),
                "url": m.get("url"),
                "section_index": m.get("section_index"),
                "section_title": m.get("section_title"),
                "section_path": m.get("section_path") or m.get("section_title") or "??",
                "chunk_index": m.get("chunk_index"),
                "text": m.get("text", ""),
            }
        )
    if trace_retrieval and trace is not None:
        trace["final_evidence_cnt"] = len(results)
        id2idx = None
        eval_rank = None
        target_item = None
        if eval_target_id is not None:
            for k, ev in enumerate(results, start=1):
                idx = ev.get("idx")
                if idx is None:
                    if id2idx is None:
                        id2idx = {}
                        for j, mm in enumerate(METAS):
                            _id = mm.get("id")
                            if _id:
                                id2idx[_id] = j
                    idx = id2idx.get(ev.get("id"))
                if isinstance(idx, int) and idx == eval_target_id:
                    m = METAS[idx] if 0 <= idx < len(METAS) else None
                    if not (0 <= idx < len(METAS)):
                        src = "unknown"
                    elif idx in forced_set:
                        src = "forced"
                    elif idx in prior_set:
                        src = "title"
                    else:
                        src = "vec"
                    eval_rank = k
                    target_item = {
                        "rank": k,
                        "idx": idx,
                        "id": (m.get("id") if m else ev.get("id")),
                        "title": (m.get("title") if m else ev.get("title")),
                        "section_path": ((m.get("section_path") or m.get("section_title") or "") if m else (ev.get("section_path") or "")),
                        "src": src,
                        "global_score": (global_score_map.get(idx) if isinstance(idx, int) else None),
                        "vec_score": (vec_scores.get(idx) if isinstance(idx, int) else None),
                        "text_head": ((m.get("text") or "")[:160] if m else (ev.get("text") or "")[:160]),
                    }
                    break
        top_items = []
        for i, ev in enumerate(results[:30], start=1):
            idx = ev.get("idx")
            if idx is None:
                if id2idx is None:
                    id2idx = {mm.get("id"): j for j, mm in enumerate(METAS) if mm.get("id")}
                idx = id2idx.get(ev.get("id"))
            m = METAS[idx] if isinstance(idx, int) and 0 <= idx < len(METAS) else None
            if not isinstance(idx, int) or idx < 0 or idx >= len(METAS):
                src = "unknown"
            elif idx in forced_set:
                src = "forced"
            elif idx in prior_set:
                src = "title"
            else:
                src = "vec"
            top_items.append(
                {
                    "rank": ev.get("rank") or i,
                    "idx": idx,
                    "id": (m.get("id") if m else ev.get("id")),
                    "title": (m.get("title") if m else ev.get("title")),
                    "section_path": ((m.get("section_path") or m.get("section_title") or "") if m else (ev.get("section_path") or "")),
                    "src": src,
                    "global_score": (global_score_map.get(idx) if isinstance(idx, int) else None),
                    "vec_score": (vec_scores.get(idx) if isinstance(idx, int) else None),
                    "text_head": ((m.get("text") or "")[:160] if m else (ev.get("text") or "")[:160]),
                }
            )
        trace["final_evidence_top30"] = top_items
        trace["eval_target_final_rank"] = eval_rank
        trace["final_evidence_target_item"] = target_item
    if trace_retrieval and trace is not None:
        if trace_out_path:
            from pathlib import Path
            out_path = Path(trace_out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_name(out_path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(trace, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            os.replace(str(tmp_path), str(out_path))
        else:
            print(json.dumps(trace, ensure_ascii=False), file=sys.stderr)
    return results
# ============================================================
# LLM: 回答
# ============================================================
def build_messages(
    question: str,
    evidences: List[Dict],
    mode: str,
    need_version: bool,
    detail_level: str,
    subquestions: List[str],
) -> List[Dict]:
    evidences_for_llm = evidences[:EVIDENCE_FOR_LLM]
    renum = []
    for i, ev in enumerate(evidences_for_llm, start=1):
        ev2 = dict(ev)
        ev2["rank"] = i
        renum.append(ev2)
    evidences_for_llm = renum
    evidence_lines = []
    for i, ev in enumerate(evidences_for_llm, start=1):
        txt = (ev.get("text") or "").strip()

        if _is_trade_evidence(ev):
            txt = _truncate_trade_text(txt, TRADE_EVIDENCE_TEXT_MAX)
        else:
            if len(txt) > EVIDENCE_TEXT_MAX:
                txt = txt[:EVIDENCE_TEXT_MAX] + "…"
        evidence_lines.append(
            f"证据#{i} 标题：{ev['title']}\n"
            f"章节：{ev['section_path']}\n"
            f"内容：{txt}\n"
        )
    rules = [
        "你是一个严格基于证据回答的 Minecraft Wiki 助手。",
        "严格使用给定证据回答，不能编造，也不能用常识补全机制细节。",
        "如果证据不足，请明确说明“不确定/证据缺失”。",
        "先做术语对齐：说明问题中的关键概念在证据里对应的机制/条目；若证据只覆盖表现/效果而非提升途径，必须明确说明证据缺口。",
        "禁止强断言：除非证据明确出现“唯一/only/sole”，否则不得声称“唯一方法/只能通过…”。",
        "证据不足时：输出“缺口说明 + 需要补充的证据类型”，并给出保守结论。",
        "回答时只能引用证据#i（如 证据#1、证据#2），不得引用其他数字或原始rank。",
        "如果用户在问“应该给什么东西/怎么选择”，你需要：先用证据列出每个魔咒分别适用的物品与关键限制（互斥/版本差异），再给出推荐组合；推荐必须完全由证据推出。",
    ]
    if need_version:
        rules += [
            "必须考虑 Java版 与 基岩版 的差异：",
            "如果证据明确提到某一版本，只能对该版本下结论，并明确标注“仅Java/仅基岩”。",
            "如果证据未覆盖另一版本，必须写“另一版本证据不足”。",
        ]
    if detail_level == "brief":
        rules += ["输出尽量短：1-4条要点即可。"]
    elif detail_level == "detailed":
        rules += ["允许更详细：可分点说明，并补充关键注意事项，但每条都要有证据支撑。"]
    else:
        rules += ["默认详细程度：简洁但解释完整。"]
    if mode == "overview":
        rules += [
            "这是概括性问题：请给出结构化概览（例如分阶段/分目标），每个关键结论都要引用证据。",
            "如果证据只描述了目标但没有步骤，请说明“步骤证据不足”。",
        ]
    elif mode == "howto":
        rules += [
            "这是流程/建议类问题：先给出可从证据直接支持的步骤/要点（带引用）。",
            "若缺少关键步骤证据，请明确指出缺口，而不是编造步骤。",
        ]
    elif mode == "why":
        rules += [
            "这是解释/对比类问题：用证据列出机制差异点（带引用），再做简短总结。",
            "不要添加证据中没有出现的机制细节。",
        ]
    system = "\n".join(rules)
    sq = ""
    if subquestions:
        sq = "请顺便覆盖这些必要子问题（仍需证据引用）：\n- " + "\n- ".join(subquestions) + "\n\n"
    user = (
        f"问题：{question}\n\n"
        f"{sq}"
        f"证据：\n{''.join(evidence_lines)}\n"
        "请给出简洁、可验证的回答。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
from typing import List, Dict, Any

def call_llm(messages: List[Dict], *, config: Dict[str, Any]) -> str:
    api_base = (config.get("api_base") or "https://api.deepseek.com").strip()
    api_key = (config.get("api_key") or "").strip()
    # 你现在 config 里默认是 deepseek-chat；如果你想用 reasoner，就把 config 默认改掉
    model = (config.get("model") or "deepseek-chat").strip()

    if not api_key:
        raise RuntimeError("缺少 API Key：请在设置中填写（不会写入磁盘）")

    url = f"{api_base.rstrip('/')}/v1/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0.2}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    out = data["choices"][0]["message"]["content"].strip()

    sys_txt = "\n".join([m["content"] for m in messages if m.get("role") == "system"])
    user_txt = "\n".join([m["content"] for m in messages if m.get("role") == "user"])
    _add_token_usage("answer_llm", sys_txt, user_txt, out)
    return out
# ============================================================
# Main
# ============================================================
def pipeline(
    question: str,
    *,
    config: Dict[str, Any],
    vec_k: int = 2000,
    top_k: Optional[int] = None,
    trace: bool = False,
    trace_target: int = 11602,
    trace_out: Optional[str] = None,
    eval_target_title: Optional[str] = None,
    eval_target_id: Optional[int] = None,
    max_evidences: Optional[int] = None,          # 默认 None -> 用 EVIDENCE_FOR_LLM
    max_chars_per_evidence: int = 600,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    单一真相：把 main() 的核心流程搬到这里。
    返回结构化结果，供 CLI / FastAPI / Tauri 共用。
    """
    trace_obj: Optional[Dict[str, Any]] = {} if trace else None
    used_max_evidences = int(max_evidences) if max_evidences is not None else int(EVIDENCE_FOR_LLM)
    def _progress(msg: str):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    # ---- 环境/初始化（保持与你 main 行为一致） ----
    if trace_out:
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    if not INDEX_PATH.exists() or not META_PATH.exists():
        raise RuntimeError("索引文件不存在，请先运行 03_build_index.py")

    init_once()
    reset_token_stats()

    q = (question or "").strip()
    if not q:
        raise ValueError("question is empty")

    # ---- 1) 分类 & 抽计划 ----
    _progress("正在判断问题类型…")
    cls = classify_question(q, config=config)
    mode = cls["type"]
    need_version = cls["version_sensitive"]

    _progress("正在生成检索计划…")
    qp = extract_query_plan(q, mode=mode, config=config)
    detail_level = qp.get("detail_level", "normal")
    subquestions = qp.get("subquestions", [])
    need_version = bool(qp.get("need_version", need_version)) or need_version

    retrieval_query = build_retrieval_query(qp)

    # ---- 2) 组 plan（完全复刻 main 的逻辑） ----
    plan = dict(STRATEGIES.get(mode, STRATEGIES["fact"]))

    if top_k is not None:
        plan["top_k"] = int(top_k)
        if int(top_k) >= 60:
            plan["max_per_title"] = max(int(plan.get("max_per_title", 1)), 4)
            plan["rerank_use_title_section"] = True
            plan["global_rerank_use_mmr"] = True
            plan["mmr_lambda"] = 0.75
            plan["cap_titles_for_prior"] = True
            plan["auto_fill_topk"] = True
            plan["extra_vec_per_title"] = 2
            plan["extra_prior_per_title"] = 2
            if eval_target_id is not None or eval_target_title:
                plan["boost_eval_title_candidates"] = 30
            plan["global_pool_boost_mul"] = 10
            plan["global_pool_boost_cap"] = 5000
            plan["page_boost_enabled"] = True
            plan["page_boost_alpha"] = 0.15
            plan["page_boost_topn"] = 5
            plan["page_boost_mode"] = "max"

    plan["vec_k"] = int(vec_k)
    plan["trace_retrieval"] = bool(trace)
    plan["trace_target_ids"] = [int(trace_target)]

    if trace_out:
        plan["trace_out"] = trace_out
    if eval_target_title:
        plan["eval_target_title"] = eval_target_title
    if eval_target_id is not None:
        plan["eval_target_id"] = int(eval_target_id)

    # ---- 3) 检索 ----
    _progress("正在从 Wiki 检索证据…")
    evidences = retrieve_with_plan(
        retrieval_query,
        plan,
        anchors=qp.get("anchors"),
    )

    # ---- 4)（可选）消歧：完全复刻 main ----
    disambig = {
        "triggered": False,
        "selections": {},
        "forced_ids": [],
    }

    if need_disambiguate(qp.get("anchors", []), evidences):
        _progress("检测到消歧迹象，正在进行义项选择…")
        anchors = qp.get("anchors", [])
        candidates_map = {
            a: build_disambig_candidates_for_anchor(a, NORM_TITLES, max_cand=10)
            for a in anchors
        }
        selections = disambiguate_anchors_with_deepseek(q, anchors, candidates_map, config=config)
        chosen_titles = [t for t in selections.values() if t]

        forced_ids = force_title_chunks(
            titles=chosen_titles,
            title2idx=TITLE2IDX,
            k_chunks_per_title=4,
            k_total=40,
        )

        disambig["triggered"] = True
        disambig["selections"] = selections
        disambig["forced_ids"] = forced_ids

        if forced_ids:
            _progress("已获得消歧结果，正在强制覆盖锚点页并重检索…")
            evidences = retrieve_with_plan(
                retrieval_query,
                plan,
                anchors=qp.get("anchors"),
                forced_ids=forced_ids,
            )

    # ---- 4.5) section aggregation ----
    evidences_for_llm = postprocess_evidences(
    evidences,
    max_evidences=used_max_evidences,
    max_chars_per_evidence=int(max_chars_per_evidence),  # 普通段落仍可 600
    debug_out=trace_obj,
)

    # ---- 5) 去噪/裁剪：完全复刻 main 的 trace_obj 逻辑 ----
    _progress("正在整理证据并组织提示词…")
    trace_obj = None
    trace_out_path = plan.get("trace_out")
    if plan.get("trace_retrieval") and trace_out_path and os.path.exists(trace_out_path):
        try:
            with open(trace_out_path, "r", encoding="utf-8") as f:
                trace_obj = json.load(f)
        except Exception:
            trace_obj = None

    # used_max_evidences = int(max_evidences) if max_evidences is not None else int(EVIDENCE_FOR_LLM)

    evidences_for_llm = enhance_sections_inplace(
    evidences_for_llm,
    META_PATH,
    only_if_section_contains="交易",
    max_chars=TRADE_EVIDENCE_TEXT_MAX,  # 交易表更长，直接拉高
)

    evidences_for_llm = expand_evidence_context_fast(
        evidences_for_llm,
        META_PATH,
        window=2,
        max_chars=EVIDENCE_TEXT_MAX,
        trade_max_chars=TRADE_EVIDENCE_TEXT_MAX,
    )

    if plan.get("trace_retrieval") and trace_obj is not None and trace_out_path:
        tmp_path = trace_out_path + ".tmp"
        Path(trace_out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(trace_obj, ensure_ascii=False, indent=2))
        os.replace(tmp_path, trace_out_path)

    # ---- 6) LLM ----
    messages = build_messages(
        question=q,
        evidences=evidences_for_llm,
        mode=mode,
        need_version=need_version,
        detail_level=detail_level,
        subquestions=subquestions,
    )

    _progress("正在调用 DeepSeek 生成回答…")
    answer = call_llm(messages, config=config)
    if (config.get("debug_mode")):
        print ("[DEBUG]Details informations of this answer:\n")
        print ("[DEBUG]mode:",mode,'\n')
        print ("[DEBUG]need_version",need_version,'\n')
        print ("[DEBUG]detail_level",detail_level,'\n')
        print ("[DEBUG]anchors",qp.get("anchors", []),'\n')
        print ("[DEBUG]subquestions",subquestions,'\n')

    # ---- 7) 返回结构化结果 ----
    return {
        "answer": answer,
        "debug": {
            "mode": mode,
            "need_version": need_version,
            "detail_level": detail_level,
            "anchors": qp.get("anchors", []),
            "rewrite_query": qp.get("rewrite_query"),
            "subquestions": subquestions,
            "retrieval_query": retrieval_query,
            "plan": plan,
            "disambiguation": disambig,
        },
        "evidences_raw": evidences,                 # 全量（可能 30/80/whatever）
        "evidences_for_llm": evidences_for_llm,     # 去噪后喂 LLM 的那批
        "stats": format_cost_stats(),               # 复用你已有的 token/cost 统
        "token_usage":format_cost_stats_dict(),
    }
def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=str, default=None)
    parser.add_argument("--interactive", action="store_true")

    parser.add_argument("--vec-k", type=int, default=2000)
    parser.add_argument("--top-k", type=int, default=None)

    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--trace-target", type=int, default=11602)  # 建议跟 pipeline 默认一致
    parser.add_argument("--trace-out", type=str, default=None)

    parser.add_argument("--eval-target-title", type=str, default=None)
    parser.add_argument("--eval-target-id", type=int, default=None)

    parser.add_argument("--max-evidences", type=int, default=None)
    parser.add_argument("--max-chars-per-evidence", type=int, default=600)

    # ✅ 新增：CLI 也能直接传（不落盘）
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--api-base", type=str, default=None)
    parser.add_argument("--model", type=str, default=None)

    args = parser.parse_args(argv)

    # ✅ 组装 config，空值不传，避免覆盖默认/环境变量
    config = {}
    if args.api_key:
        config["api_key"] = args.api_key.strip()
    if args.api_base:
        config["api_base"] = args.api_base.strip()
    if args.model:
        config["model"] = args.model.strip()

    questions = [args.question] if args.question is not None else None

    while True:
        if questions is not None:
            if not questions:
                break
            q = questions.pop(0)
        else:
            q = input("\nQuestion (q to quit) > ").strip()
            if q.lower() == "q":
                break

        try:
            result = pipeline(
                q,
                config=config,  # ✅ 关键：补上 config
                vec_k=args.vec_k,
                top_k=args.top_k,
                trace=args.trace,
                trace_target=args.trace_target,
                trace_out=args.trace_out,
                eval_target_title=args.eval_target_title,
                eval_target_id=args.eval_target_id,
                max_evidences=args.max_evidences,
                max_chars_per_evidence=args.max_chars_per_evidence,
                progress_cb=None,
            )
            print(result)

        except Exception as e:
            print("运行失败：", e)
            traceback.print_exc()
            continue

        if questions is not None:
            break


if __name__ == "__main__":
    main()