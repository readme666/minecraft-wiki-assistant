import json
from pathlib import Path
from typing import List

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
import re
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CHUNKS_DIR = PROJECT_ROOT / "chunks"

CHUNKS_PATH = CHUNKS_DIR / "chunks_all.jsonl"
INDEX_DIR = PROJECT_ROOT / "index"
INDEX_PATH = INDEX_DIR / "faiss_all.index"
META_PATH = INDEX_DIR / "meta_all.jsonl"

# ✅ 适配 chunk 阶段的表格标记（你前面已经输出成这个了）
WIKITABLE_TAG_RE = re.compile(r"\[WIKITABLE\]\s*([\s\S]*?)\s*\[/WIKITABLE\]", re.M)

# ✅ 常见“无参数模板”映射：至少别吞掉绿宝石/货币
_SIMPLE_TEMPLATE_MAP = {
    "Emerald": "Emerald",
    "emerald": "Emerald",
    "绿宝石": "绿宝石",
    "綠寶石": "綠寶石",
    "钻石": "钻石",
    "鑽石": "鑽石",
    "Diamond": "Diamond",
    "diamond": "Diamond",
}

def strip_wiki_markup(s: str) -> str:
    """用于清洗表格单元格或残留 wiki 语法；不要吞掉关键货币/物品模板。"""
    if not s:
        return ""

    # refs
    s = re.sub(r"<ref[^>]*>[\s\S]*?</ref>", "", s)
    s = re.sub(r"<ref[^/]*/>", "", s)

    # ✅ 无参数模板：{{Emerald}} / {{绿宝石}} -> 文本
    def _simple_tpl(m: re.Match) -> str:
        name = (m.group(1) or "").strip()
        return _SIMPLE_TEMPLATE_MAP.get(name, name)  # 未命中也别原样 {{}}，直接取 name

    s = re.sub(r"\{\{\s*([^|{}]+?)\s*\}\}", _simple_tpl, s)

    # ✅ 常见：{{Item|xxx}} -> xxx
    s = re.sub(r"\{\{\s*[Ii]tem\s*\|\s*([^}|]+).*?\}\}", r"\1", s)

    # 你原来已有的
    s = re.sub(r"\{\{ItemLink\|([^}|]+).*?\}\}", r"\1", s)
    s = re.sub(r"\{\{hp\|([^}|]+).*?\}\}", r"\1", s)
    s = re.sub(r"\{\{convert\|([^}|]+)\|([^}|]+)\|([^}|]+)[^}]*\}\}", r"\1 \2", s)

    # wiki links
    s = re.sub(r"\[\[([^|\]#]+)(#[^|\]]+)?\|([^\]]+)\]\]", r"\3", s)
    s = re.sub(r"\[\[([^|\]]+)\]\]", r"\1", s)

    # ✅ 最后兜底：删复杂模板（前面尽量提取有用信息了）
    s = re.sub(r"\{\{[\s\S]*?\}\}", "", s)

    # 空白
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def markdown_table_to_rows(md: str) -> List[str]:
    """
    把 markdown 表格变成更适合向量化的“行文本”：
    - 忽略分隔行 | --- |
    - 输出: "表格行: 列1=...; 列2=...; ..."
    """
    lines = [ln.strip() for ln in md.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    # 只处理标准表格
    if not (lines[0].startswith("|") and "|" in lines[0]):
        return []

    header = [c.strip() for c in lines[0].strip("|").split("|")]
    header = [strip_wiki_markup(h) for h in header]

    out = []
    for ln in lines[2:]:  # 跳过 header + --- 分隔
        if not (ln.startswith("|") and "|" in ln):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        cells = [strip_wiki_markup(c) for c in cells]

        # 对齐列数
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        if len(cells) > len(header):
            cells = cells[: len(header)]

        pairs = [f"{h}={v}" for h, v in zip(header, cells) if h and v]
        if pairs:
            out.append("表格行: " + "; ".join(pairs))
    return out

def normalize_tables_for_embedding(text: str) -> str:
    """
    把 [WIKITABLE] markdown 表格替换为多行“表格行:”文本，
    避免模型只看到一坨 | | | ，提升检索命中率。
    """
    def _repl(m: re.Match) -> str:
        md = m.group(1)
        rows = markdown_table_to_rows(md)
        if not rows:
            # 表格解析失败就保留原 md（至少别丢信息）
            return "\n" + md + "\n"
        return "\n" + "\n".join(rows) + "\n"

    return WIKITABLE_TAG_RE.sub(_repl, text)

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def main():
    if not CHUNKS_PATH.exists():
        print("未找到输入文件:", CHUNKS_PATH)
        return

    ensure_dir(INDEX_DIR)
    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    texts: List[str] = []
    metas: List[dict] = []

    with CHUNKS_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            raw = rec.get("text", "")

            # ✅ 关键：用 chunk 里的 [WIKITABLE] 做“行文本化”，并且不会吞掉绿宝石模板
            clean = normalize_tables_for_embedding(raw)

            texts.append(clean)
            metas.append(
                {
                    k: rec.get(k)
                    for k in [
                        "id",
                        "title",
                        "url",
                        "section_index",
                        "section_title",
                        "section_path",
                        "section_level",
                        "chunk_index",
                    ]
                }
            )

    print("开始编码 chunks:", len(texts))
    emb = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
    )
    emb = np.asarray(emb, dtype="float32")

    dim = emb.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(emb)

    faiss.write_index(index, str(INDEX_PATH))

    with META_PATH.open("w", encoding="utf-8") as f:
        for m, t in zip(metas, texts):
            m2 = dict(m)
            m2["text"] = t
            f.write(json.dumps(m2, ensure_ascii=False) + "\n")

    print("完成:", INDEX_PATH, META_PATH)

if __name__ == "__main__":
    main()