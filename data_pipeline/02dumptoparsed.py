import json
import random
import re
import requests
from pathlib import Path
from typing import Dict, List, Optional
import time
try:
    import mwparserfromhell  # type: ignore
    _HAS_MW = True
except Exception:
    mwparserfromhell = None
    _HAS_MW = False
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
IN_FILE = DATA_DIR / "data_dump.jsonl"
OUT_FILE = DATA_DIR / "data_parsed.jsonl"
EXPAND_CACHE_FILE = Path("expand_cache.jsonl")   # 可选：不想落盘就注释掉相关写入/读取
EXPAND_CACHE_MAX = 5000                          # 内存最多缓存条目数（可调）
_expand_cache: OrderedDict[str, str] = OrderedDict()

# 内存 LRU cache
_expand_cache_mem: OrderedDict[str, str] = OrderedDict()

# 启动时加载磁盘缓存（如果存在）
def _expand_cache_key(title: str, text: str) -> str:
    h = hashlib.sha1()
    h.update(title.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8"))
    return h.hexdigest()

def _expand_cache_get(k: str) -> str | None:
    v = _expand_cache.get(k)
    if v is None:
        return None
    _expand_cache.move_to_end(k)
    return v

def _expand_cache_put(k: str, v: str) -> None:
    _expand_cache[k] = v
    _expand_cache.move_to_end(k)
    if len(_expand_cache) > EXPAND_CACHE_MAX:
        _expand_cache.popitem(last=False)

def _expand_cache_load() -> None:
    if not EXPAND_CACHE_FILE.exists():
        return
    try:
        with EXPAND_CACHE_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                k = obj.get("k")
                v = obj.get("v")
                if isinstance(k, str) and isinstance(v, str):
                    _expand_cache_put(k, v)
    except Exception:
        pass

def _expand_cache_append(k: str, v: str) -> None:
    try:
        with EXPAND_CACHE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"k": k, "v": v}, ensure_ascii=False) + "\n")
    except Exception:
        pass

# 程序启动时加载一次（想禁用磁盘缓存就把这一行注释掉）
_expand_cache_load()

SESSION = requests.Session()


from functools import lru_cache

WIKI_API = "https://zh.minecraft.wiki/api.php"
API_URL = "https://zh.minecraft.wiki/api.php"
UA = "local-rag/0.1 (contact: you@example.com)"


def expand_templates(title: str, text: str, timeout: int = 30, *,
                     retries: int = 4,
                     backoff_base: float = 1.2,
                     max_text_len: int = 120000) -> str:
    """
    调 MediaWiki API expandtemplates，把模板展开。
    - 内存 LRU + 可选磁盘缓存
    - 自动重试（含 429 / 5xx / 超时）
    - 失败最终返回原文，避免全批崩
    - 超长文本直接跳过展开（返回原文），减少超时/限流
    """

    # ✅ 先查缓存（命中直接返回，不走网络）
    k = _expand_cache_key(title, text)
    cached = _expand_cache_get(k)
    if cached is not None:
        return cached

    # 超长直接不展开
    if len(text) > max_text_len:
        # （可选）也缓存一下：避免重复判断 + 也避免之后重复进入函数
        _expand_cache_put(k, text)
        # _expand_cache_append(k, text)  # 可选
        return text

    payload = {
        "action": "expandtemplates",
        "format": "json",
        "prop": "wikitext",
        "title": title,
        "text": text,
    }

    for attempt in range(retries + 1):
        try:
            r = SESSION.post(API_URL, data=payload, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise requests.HTTPError(f"HTTP {r.status_code}", response=r)

            r.raise_for_status()
            data = r.json()

            try:
                out = data["expandtemplates"]["wikitext"]
            except Exception:
                out = text

            # ✅ 写缓存
            _expand_cache_put(k, out)
            _expand_cache_append(k, out)  # 不想落盘就注释掉
            return out

        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            if attempt >= retries:
                # ✅ 失败也缓存（避免同一段反复超时重试）
                _expand_cache_put(k, text)
                # _expand_cache_append(k, text)  # 可选
                return text

            sleep_s = (backoff_base ** attempt) + random.random() * 0.3

            # 如果是 429，尊重 Retry-After（如果有）
            try:
                resp = getattr(e, "response", None)
                if resp is not None and getattr(resp, "status_code", None) == 429:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        sleep_s = max(sleep_s, float(ra))
            except Exception:
                pass

            time.sleep(sleep_s)

        except Exception:
            _expand_cache_put(k, text)
            # _expand_cache_append(k, text)  # 可选
            return text

    _expand_cache_put(k, text)
    # _expand_cache_append(k, text)  # 可选
    return text
def _strip_markup(text: str) -> str:
    if _HAS_MW:
        try:
            code = mwparserfromhell.parse(text)
            return code.strip_code(normalize=True, collapse=True).strip()
        except Exception:
            return text.strip()
    # regex fallback
    s = text
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"''+", "", s)
    return s.strip()


def _strip_attr_prefix(cell: str) -> str:
    s = cell.strip()
    # only strip attribute prefix like 'style=... | value'
    if "|" in s:
        left, right = s.split("|", 1)
        left = left.strip()
        # Only treat left side as attribute list if it is a sequence of key=value pairs.
        # This avoids stripping wikilinks/templates like [[A|B]] or {{...|...}}.
        if "[[" not in left and "{{" not in left:
            if re.fullmatch(r'(?:\s*\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^"\']\S*)\s*)+', left):
                return right.strip()
    return s


import re
from typing import List, Optional, Tuple

_COLSPAN_RE = re.compile(r'\bcolspan\s*=\s*"?(?P<n>\d+)"?', re.IGNORECASE)

def _strip_attr_prefix_and_span(cell: str) -> Tuple[str, int]:
    """
    解析类似： 'colspan="2" | 12'  → ('12', 2)
    其他情况 → (原值, 1)
    """
    s = cell.strip()
    span = 1

    # 只在“属性前缀 | 值”的格式下处理
    if "|" in s:
        left, right = s.split("|", 1)
        left = left.strip()
        right = right.strip()

        # 避免误伤 [[A|B]] 或 {{...|...}}
        if "[[" not in left and "{{" not in left:
            m = _COLSPAN_RE.search(left)
            if m:
                try:
                    span = max(1, int(m.group("n")))
                except Exception:
                    span = 1
            # 把属性前缀剥掉
            return right, span

    return s, span


def parse_wikitable(wikitext: str) -> List[str]:
    lines = [ln.rstrip("\n") for ln in wikitext.splitlines()]
    if not lines:
        return []

    caption: Optional[str] = None
    headers: List[str] = []
    rows: List[List[str]] = []

    cur: List[str] = []
    cur_is_header_row = False
    seen_data = False  # 一旦见到数据行，就不再把 '!' 当“表头行”处理

    def emit_cells(parts: List[str]):
        nonlocal cur
        for p in parts:
            val, span = _strip_attr_prefix_and_span(p)
            val = _strip_markup(val)
            if not val:
                continue
            for _ in range(span):
                cur.append(val)

    def flush_row():
        nonlocal cur, cur_is_header_row, headers, rows, seen_data
        if not cur:
            cur_is_header_row = False
            return

        if cur_is_header_row and not headers:
            # 只在“还没有 headers”的时候写入表头（避免被行标题覆盖）
            headers = cur[:]
        else:
            rows.append(cur[:])
            seen_data = True

        cur = []
        cur_is_header_row = False

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("{|") or s.startswith("|}"):
            continue
        if s.startswith("|-"):
            flush_row()
            continue
        if s.startswith("|+"):
            # caption
            cap_raw = s.lstrip("|+").strip()
            cap_raw, _ = _strip_attr_prefix_and_span(cap_raw)
            caption = _strip_markup(cap_raw)
            continue

        # 表头行：优先用 "!!" 判断（更像真正表头）
        if s.startswith("!"):
            # 如果已经进入数据区，则把 '!' 当普通单元格（行表头/普通头单元格）
            if (not seen_data) and ("!!" in s):
                cur_is_header_row = True
                parts = [p.strip() for p in s.lstrip("!").split("!!")]
                emit_cells(parts)
            else:
                # 当作普通单元格，支持 '! a !! b' 或 '! a'
                parts = [p.strip() for p in s.lstrip("!").split("!!")]
                emit_cells(parts)
            continue

        if s.startswith("|"):
            parts = [p.strip() for p in s.lstrip("|").split("||")]
            emit_cells(parts)
            continue

        # 多行续写：拼到最后一个单元格
        if cur:
            cur[-1] = (cur[-1] + " " + _strip_markup(s)).strip()

    flush_row()

    if not rows and not headers:
        return []

    out: List[str] = []
    if caption:
        out.append(f"表格: {caption}")

    # 输出策略：优先保证“可检索”
    # - 如果 headers 质量不高，就直接按行输出
    if headers and len(headers) >= 2:
        for r in rows:
            # 对齐到 headers 的长度（截断或补空）
            rr = r[:len(headers)] + [""] * max(0, len(headers) - len(r))
            items = [f"{headers[i]}: {rr[i]}" for i in range(len(headers)) if rr[i]]
            if items:
                out.append(" | ".join(items))
    else:
        for r in rows:
            if r:
                out.append(" | ".join(r))

    return out

def _extract_for_targets(text: str) -> List[str]:
    targets: List[str] = []
    for m in re.finditer(r"\{\{\s*for\|([^}]+)\}\}", text, flags=re.IGNORECASE):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        if parts:
            targets.append(parts[-1])
    return targets


def _extract_exclusive(text: str) -> List[str]:
    items: List[str] = []
    for m in re.finditer(r"\{\{\s*exclusive\|([^}]+)\}\}", text, flags=re.IGNORECASE):
        parts = [p.strip() for p in m.group(1).split("|") if p.strip()]
        if parts:
            items.append("/".join(parts))
    return items


def _convert_headings(text: str) -> str:
    out = []
    for ln in text.splitlines():
        m = re.match(r"^(=+)\s*(.*?)\s*\1\s*$", ln)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            if title:
                out.append("#" * max(1, level - 1) + " " + title)
            continue
        out.append(ln)
    return "\n".join(out)


def _strip_code_fallback(text: str) -> str:
    s = text
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"''+", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    return s


def clean_wikitext(text: str, append_meta: bool = True) -> str:
    # convert headings early
    text = _convert_headings(text)

    # pre-handle tables: replace wikitable blocks with parsed rows
    out_lines: List[str] = []
    buf: List[str] = []
    in_table = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("{|"):
            in_table = True
            buf = [ln]
            continue
        if in_table:
            buf.append(ln)
            if ln.lstrip().startswith("|}"):
                in_table = False
                parsed = parse_wikitable("\n".join(buf))
                if parsed:
                    out_lines.extend(parsed)
                buf = []
            continue
        out_lines.append(ln)
    if buf:
        parsed = parse_wikitable("\n".join(buf))
        if parsed:
            out_lines.extend(parsed)

    text = "\n".join(out_lines)
    for_targets = _extract_for_targets(text)
    exclusive = _extract_exclusive(text)

    if _HAS_MW:
        code = mwparserfromhell.parse(text)

        # 1) remove templates
        def _tpl_name(tpl) -> str:
            try:
                return str(tpl.name).strip()
            except Exception:
                return ""

        def _clean_tpl_value(v: str) -> str:
            s = re.sub(r"<\s*br\s*/?>", " ", v, flags=re.IGNORECASE)
            try:
                s = mwparserfromhell.parse(s).strip_code(normalize=True, collapse=True)
            except Exception:
                pass
            s = re.sub(r"\s+", " ", s).strip()
            s = s.replace("–", "-").replace("—", "-")
            return s

        def _tpl_args_kv(tpl, limit: int = 12) -> list[tuple[str, str]]:
            """返回 (key, value) 列表；key 可能是 '1','2' 或具名参数。"""
            out = []
            try:
                for a in tpl.params:
                    k = str(a.name).strip()
                    v = str(a.value).strip()
                    if not v:
                        continue
                    out.append((k, v))
                    if len(out) >= limit:
                        break
            except Exception:
                pass
            return out

        def _render_trade_line(tpl) -> str:
            kv = dict(_tpl_args_kv(tpl, limit=64))
            keys = [
                "lvl",
                "slot",
                "want",
                "want2",
                "wantQuant",
                "wantQuant2",
                "give",
                "giveQuant",
                "multi",
                "maxTrades",
                "xpGain",
                "giveNote",
                "giveNoteText",
            ]
            parts = []
            for k in keys:
                v = kv.get(k)
                if not v:
                    continue
                v = _clean_tpl_value(v)
                if v:
                    parts.append(f"{k}={v}")
            if not parts:
                return ""
            return "交易: " + " | ".join(parts)

        # 1) expand TradeTable -> TradeLine rows (search-friendly)
        for tpl in list(code.filter_templates(recursive=True)):
            try:
                if _tpl_name(tpl).lower() != "tradetable":
                    continue
                lines = []
                for p in tpl.params:
                    try:
                        vcode = mwparserfromhell.parse(str(p.value))
                        for t2 in vcode.filter_templates(recursive=True):
                            if _tpl_name(t2).lower() == "tradeline":
                                ln = _render_trade_line(t2)
                                if ln:
                                    lines.append(ln)
                    except Exception:
                        continue
                code.replace(tpl, "\n".join(lines))
            except Exception:
                pass

        def _render_tpl_fallback(tpl) -> str:
            """未知模板兜底：模板:Name k=v ...  (保证 wantQuant=8-22 之类不丢)"""
            name = _tpl_name(tpl)
            kv = _tpl_args_kv(tpl, limit=16)
            if not name:
                return ""
            parts = []
            for k, v in kv:
                # 简单降噪：把换行压扁，避免一行爆炸
                v = _clean_tpl_value(v)
                if not v:
                    continue
                parts.append(f"{k}={v}")
            if parts:
                return f"模板:{name} " + " ".join(parts)
            return f"模板:{name}"

        def _render_tpl_semantic(tpl) -> str | None:
            """少数高价值模板：输出更可读的一行。返回 None 表示不命中，交给 fallback。"""
            name = _tpl_name(tpl).lower()

            # 交易：TradeLine（你要的 wantQuant=8-22 就在这里）
            if name == "tradeline":
                return _render_trade_line(tpl)

            # 历史：HistoryLine（简单保留版本/文本）
            if name == "historyline":
                kv = dict(_tpl_args_kv(tpl, limit=64))
                # HistoryLine 有很多是位置参数；这里直接 fallback 也能搜到版本号
                # 但做个轻语义化：把 dev / 版本 / 描述拼出来
                dev = kv.get("dev", "")
                # 位置参数常用 1/2/3...，不保证存在
                desc = kv.get("4") or kv.get("3") or kv.get("2") or ""
                if dev or desc:
                    d = re.sub(r"\s+", " ", desc).strip()
                    return "历史: " + " ".join([x for x in [dev, d] if x])
                return None

            # 音效表：Sound table（保留 subtitle/id/description）
            if name in ("sound table", "sound_table", "soundtable"):
                kv = dict(_tpl_args_kv(tpl, limit=64))
                desc = kv.get("description", "")
                sid = kv.get("id", "")
                sub = kv.get("subtitle", "")
                parts = []
                if desc: parts.append(f"desc={re.sub(r'\\s+', ' ', desc).strip()}")
                if sid: parts.append(f"id={sid}")
                if sub: parts.append(f"subtitle={re.sub(r'\\s+', ' ', sub).strip()}")
                if parts:
                    return "音效: " + " ".join(parts)
                return None

            return None


        # 1) templates: 不再删除；改为“语义化/兜底文本”
        for tpl in list(code.filter_templates(recursive=True)):
            try:
                rendered = _render_tpl_semantic(tpl)
                if rendered is None:
                    rendered = _render_tpl_fallback(tpl)

                # 可选：对明显的导航/装饰模板直接清空，降噪
                tname = _tpl_name(tpl).strip().lower()
                if tname.startswith(("navbox", "infobox", "coord", "authority control")):
                    rendered = ""

                code.replace(tpl, rendered)
            except Exception:
                pass
        # 2) remove File/Category links, keep display text for others
        for link in list(code.filter_wikilinks(recursive=True)):
            title = str(link.title).strip().lower()
            if title.startswith(("file:", "category:")):
                try:
                    code.replace(link, "")
                except Exception:
                    pass

        # 3) to plain text
        txt = code.strip_code(normalize=True, collapse=True)
    else:
        txt = _strip_code_fallback(text)

    # 4) cleanup empty lines
    txt = "\n".join(line.strip() for line in txt.splitlines())
    txt = "\n".join(line for line in txt.splitlines() if line)

    if append_meta:
        if for_targets:
            txt += "\n参见: " + "；".join(for_targets)
        if exclusive:
            txt += "\n版本限定: " + "；".join(exclusive)

    return txt


def _convert_sections(page_title: str, sections) -> List[Dict]:
    if not isinstance(sections, list):
        return []
    out: List[Dict] = []
    for sec in sections:
        if not isinstance(sec, dict):
            continue
        title = (sec.get("title") or "").strip()
        parent = sec.get("parent")
        parent = parent.strip() if isinstance(parent, str) else None
        try:
            level = int(sec.get("level", 2))
        except Exception:
            level = 2

        raw_text = sec.get("text") or ""
        # ✅ 删掉这行：raw_text = expand_templates(page_title, raw_text)

        cleaned = clean_wikitext(raw_text, append_meta=False)
        out.append(
            {
                "title": title,
                "level": level,
                "parent": parent,
                "text": cleaned,
            }
        )
    return out

def main():
    n = 0

    with IN_FILE.open(encoding="utf-8") as fin, \
         OUT_FILE.open("w", encoding="utf-8") as fout:

        for line_no, line in enumerate(fin, 1):
            obj = json.loads(line)
            title = obj.get("title", "")

            if line_no % 50 == 0:
                print(f"[{line_no}] processing: {title}")

            raw = obj.get("wikitext") or ""

            t1 = time.time()
            raw_exp = expand_templates(title, raw)   # ✅ 只 expand 一次
            print(f"  expand {title}  {time.time()-t1:.2f}s  len={len(raw_exp)}")

            t2 = time.time()
            cleaned = clean_wikitext(raw_exp)
            print(f"  clean  {title}  {time.time()-t2:.2f}s  len={len(cleaned)}")

            rec = {
                "title": obj["title"],
                "url": obj["url"],
                "text": cleaned,
                "redirect_targets": _extract_for_targets(raw),  # raw 或 raw_exp 都行；看你想提取“原始重定向模板”还是展开后的
                "sections": _convert_sections(title, obj.get("sections")),
            }

            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1

    print(f"\u2705 \u5b8c\u6210: {OUT_FILE} pages={n}")


if __name__ == "__main__":
    main()
