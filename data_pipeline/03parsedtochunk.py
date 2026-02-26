import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CHUNKS_DIR = PROJECT_ROOT / "chunks"
IN_FILE =DATA_DIR / "data_parsed.jsonl"

_SIMPLE_TEMPLATE_MAP = {
    "Emerald": "Emerald",
    "emerald": "Emerald",
    "绿宝石": "绿宝石",
    "綠寶石": "綠寶石",
    "钻石": "钻石",
    "鑽石": "鑽石",
    "Diamond": "Diamond",
}

WIKITABLE_RE = re.compile(r"\{\|[\s\S]*?\|\}", re.M)

# --------- 交易行规范化 ----------
TRADE_PREFIX_RE = re.compile(r"^\s*交\s*易\s*[:：]\s*", re.I)
TRADE_PREFIX_RE2 = re.compile(r"^\s*交易\s*[:：]\s*", re.I)

def is_trade_head_line(s: str) -> bool:
    s = s.strip()
    return bool(TRADE_PREFIX_RE.match(s) or TRADE_PREFIX_RE2.match(s))

def normalize_trade_line(line: str, default_currency: str = "Emerald") -> str:
    raw = line.strip()
    if not is_trade_head_line(raw):
        return line

    rest = TRADE_PREFIX_RE.sub("", raw)
    rest = TRADE_PREFIX_RE2.sub("", rest)

    # 分隔符统一
    rest = re.sub(r"\s*\|\s*", " | ", rest)
    rest = re.sub(r"\s+", " ", rest).strip()

    has_want = re.search(r"\bwant=", rest) is not None
    has_want_quant = "wantQuant=" in rest

    if has_want_quant and (not has_want):
        if "slot=" in rest:
            rest = re.sub(
                r"(slot=\d+)\s*\|\s*",
                r"\1 | want=%s | " % default_currency,
                rest,
                count=1,
            )
        else:
            rest = f"want={default_currency} | " + rest

    return "交易: " + rest


def normalize_trade_lines(text: str, default_currency: str = "Emerald") -> str:
    """
    更激进地修复“交易记录被换行拆开”的情况：
    - 一旦进入 trade（遇到 trade head），后续非空行默认都当作续行拼回去
    - 只有遇到新的 trade head 或空行才 flush
    这样可以吞掉你看到的那种：0.2 | maxTrades=...
    """
    if not text:
        return ""

    lines = text.splitlines()
    out: List[str] = []
    cur: Optional[str] = None

    def flush():
        nonlocal cur
        if cur is not None:
            out.append(normalize_trade_line(cur, default_currency=default_currency))
            cur = None

    for ln in lines:
        raw = ln.rstrip("\n")
        s = raw.strip()

        # 空行：结束当前 trade
        if not s:
            flush()
            continue

        # 新 trade 起点：先 flush 再开新 trade
        if is_trade_head_line(s):
            flush()
            cur = s
            continue

        # 不是 trade head
        if cur is not None:
            # ✅ 只要在积累 trade，就拼回去（最关键）
            cur += " " + s
            continue

        # 普通行
        out.append(raw)

    flush()
    return "\n".join(out).strip()


# --------- wiki 清洗 ----------
def strip_wiki_markup(s: str) -> str:
    if not s:
        return ""

    s = re.sub(r"<ref[^>]*>[\s\S]*?</ref>", "", s)
    s = re.sub(r"<ref[^/]*/>", "", s)

    def _simple_tpl(m: re.Match) -> str:
        name = (m.group(1) or "").strip()
        return _SIMPLE_TEMPLATE_MAP.get(name, m.group(0))

    s = re.sub(r"\{\{\s*([^|{}]+?)\s*\}\}", _simple_tpl, s)
    s = re.sub(r"\{\{\s*[Ii]tem\s*\|\s*([^}|]+).*?\}\}", r"\1", s)
    s = re.sub(r"\{\{ItemLink\|([^}|]+).*?\}\}", r"\1", s)
    s = re.sub(r"\{\{hp\|([^}|]+).*?\}\}", r"\1", s)
    s = re.sub(r"\{\{convert\|([^}|]+)\|([^}|]+)\|([^}|]+)[^}]*\}\}", r"\1 \2", s)

    s = re.sub(r"\[\[([^|\]#]+)(#[^|\]]+)?\|([^\]]+)\]\]", r"\3", s)
    s = re.sub(r"\[\[([^|\]]+)\]\]", r"\1", s)

    s = re.sub(r"\{\{[\s\S]*?\}\}", "", s)

    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# --------- 表格转 markdown ----------
def parse_wikitable_to_markdown(block: str) -> str:
    lines = [ln.rstrip() for ln in block.splitlines()]
    if lines and lines[0].lstrip().startswith("{|"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "|}":
        lines = lines[:-1]

    rows: List[List[str]] = []
    cur: List[str] = []

    def flush():
        nonlocal cur
        if cur:
            rows.append(cur)
        cur = []

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("|-"):
            flush()
            continue

        if s.startswith("!"):
            s2 = s[1:].strip()
            parts = [p.strip() for p in re.split(r"\s*!!\s*", s2)]
            for p in parts:
                if "|" in p:
                    p = p.split("|", 1)[1].strip()
                cur.append(strip_wiki_markup(p))
            continue

        if s.startswith("|"):
            s2 = s[1:].strip()
            parts = [p.strip() for p in re.split(r"\s*\|\|\s*", s2)]
            for p in parts:
                if "|" in p:
                    p = p.split("|", 1)[1].strip()
                cur.append(strip_wiki_markup(p))
            continue

    flush()
    if not rows:
        return ""

    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []

    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"] * len(header)) + " |")
    for r in body:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md).strip()


# --------- 保留表格块拆分 ----------
def split_text_keep_wikitable(text: str) -> List[Tuple[str, str]]:
    if not text:
        return []
    out: List[Tuple[str, str]] = []
    last = 0
    for m in WIKITABLE_RE.finditer(text):
        a, b = m.span()
        if a > last:
            seg = text[last:a]
            if seg.strip():
                out.append(("text", seg))
        blk = m.group(0)
        if blk.strip():
            out.append(("table", blk))
        last = b
    tail = text[last:]
    if tail.strip():
        out.append(("text", tail))
    return out


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# --------- 普通 chunk：按字符 overlap ----------
def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []

    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if not buf:
            return
        chunks.append("\n".join(buf).strip())
        buf = []
        buf_len = 0

    for ln in lines:
        ln_len = len(ln) + 1
        if buf and (buf_len + ln_len) > chunk_size:
            flush()
            prev = chunks[-1]
            tail_text = prev[-overlap:] if overlap > 0 else ""
            tail_lines = [x for x in tail_text.splitlines() if x.strip()]
            buf = tail_lines[-20:]
            buf_len = sum(len(x) + 1 for x in buf)

        buf.append(ln)
        buf_len += ln_len

    flush()
    return chunks


# --------- ✅ 新增：交易块按“条目数”切 ----------
def chunk_trade_block(trade_lines: List[str], per_chunk: int = 10, overlap_trades: int = 1) -> List[str]:
    """
    trade_lines: 已经是标准化后的 '交易: ...' 行列表
    """
    if not trade_lines:
        return []

    chunks: List[str] = []
    i = 0
    n = len(trade_lines)
    while i < n:
        j = min(i + per_chunk, n)
        blk = "\n".join(trade_lines[i:j]).strip()
        if blk:
            chunks.append(blk)

        if j >= n:
            break
        # overlap：往回重叠几条 trade
        i = max(0, j - overlap_trades)

        # 防止死循环（per_chunk 很小且 overlap>=per_chunk）
        if i == j:
            i = j

    return chunks


def chunk_mixed_text(text: str, chunk_size: int = 900, overlap: int = 150,
                     trade_per_chunk: int = 10, trade_overlap: int = 1) -> List[str]:
    """
    对 text 段进行更聪明的切分：
    - 连续的“交易:”行：按条目数切块
    - 其它内容：走原来的 chunk_text
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    out_chunks: List[str] = []

    normal_buf: List[str] = []
    trade_buf: List[str] = []

    def flush_normal():
        nonlocal normal_buf
        if normal_buf:
            out_chunks.extend(chunk_text("\n".join(normal_buf), chunk_size=chunk_size, overlap=overlap))
            normal_buf = []

    def flush_trade():
        nonlocal trade_buf
        if trade_buf:
            out_chunks.extend(chunk_trade_block(trade_buf, per_chunk=trade_per_chunk, overlap_trades=trade_overlap))
            trade_buf = []

    for ln in lines:
        s = ln.strip()
        if not s:
            flush_trade()
            flush_normal()
            continue

        if s.startswith("交易:"):
            flush_normal()
            trade_buf.append(s)
        else:
            flush_trade()
            normal_buf.append(ln)

    flush_trade()
    flush_normal()
    return out_chunks


def chunk_text_with_tables(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    tokens = split_text_keep_wikitable(text)
    if not tokens:
        return []

    chunks: List[str] = []
    for kind, seg in tokens:
        if kind == "text":
            # ✅ 用新的 mixed chunk
            chunks.extend(chunk_mixed_text(seg, chunk_size=chunk_size, overlap=overlap,
                                           trade_per_chunk=10, trade_overlap=1))
        else:
            md = parse_wikitable_to_markdown(seg) or seg.strip()
            table_chunk = "[WIKITABLE]\n" + md + "\n[/WIKITABLE]"
            if len(table_chunk) > 2200:
                table_chunk = table_chunk[:2200] + "…"
            chunks.append(table_chunk)
    return chunks


def section_path(sec: Dict[str, Optional[str]]) -> str:
    p = sec.get("path")
    if p:
        return p
    if sec.get("parent"):
        return f"{sec['parent']} / {sec.get('title') or ''}".strip(" /")
    return sec.get("title") or "导言"


def normalize_section_text(sec_text: str) -> str:
    if not sec_text:
        return ""

    # 1) 先拼回断行 trade（更激进）
    sec_text = normalize_trade_lines(sec_text, default_currency="Emerald")

    # 2) 再逐行 normalize（补 want= / 统一分隔符）
    lines2: List[str] = []
    for ln in sec_text.splitlines():
        lines2.append(normalize_trade_line(ln, default_currency="Emerald"))
    sec_text = "\n".join(lines2)

    # 3) 非表格清洗，表格原样保留
    toks = split_text_keep_wikitable(sec_text)
    out_parts: List[str] = []
    for kind, seg in toks:
        if kind == "text":
            out_parts.append(strip_wiki_markup(seg))
        else:
            out_parts.append(seg)
    return "\n".join([p for p in out_parts if p.strip()])


def main():
    ensure_dir(CHUNKS_DIR)
    out_path = CHUNKS_DIR / "chunks_all.jsonl"

    if not IN_FILE.exists():
        print("未找到输入文件:", IN_FILE)
        return

    total = 0
    with out_path.open("w", encoding="utf-8") as f_out:
        with IN_FILE.open("r", encoding="utf-8") as f_in:
            for line in f_in:
                rec = json.loads(line)
                sections = rec.get("sections")

                if sections:
                    for s_index, sec in enumerate(sections):
                        raw_text = sec.get("text") or ""
                        sec_text = normalize_section_text(raw_text)

                        parts = chunk_text_with_tables(sec_text, chunk_size=900, overlap=150)
                        path = section_path(sec)

                        for i, ch in enumerate(parts):
                            total += 1
                            f_out.write(
                                json.dumps(
                                    {
                                        "id": f'{rec["title"]}__{s_index}__{i}',
                                        "title": rec["title"],
                                        "url": rec["url"],
                                        "section_index": s_index,
                                        "section_title": sec.get("title"),
                                        "section_path": path,
                                        "section_level": sec.get("level"),
                                        "chunk_index": i,
                                        "text": f"标题：{rec['title']}\n小节：{path}\n{ch}",
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                else:
                    raw_text = rec.get("text", "")
                    clean_text = normalize_section_text(raw_text)
                    chunks = chunk_text_with_tables(clean_text, chunk_size=900, overlap=150)
                    for i, ch in enumerate(chunks):
                        total += 1
                        f_out.write(
                            json.dumps(
                                {
                                    "id": f'{rec["title"]}__{i}',
                                    "title": rec["title"],
                                    "url": rec["url"],
                                    "chunk_index": i,
                                    "text": f"标题：{rec['title']}\n{ch}",
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

    print("输出:", out_path, "total_chunks=", total)


if __name__ == "__main__":
    main()