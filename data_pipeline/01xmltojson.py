import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import quote

# 1. 精准定位：获取当前脚本的绝对路径，向上推两级到达项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 2. 指向上一级的 xml 文件夹 (minecraft-wiki-assistant/xml/)
XML_DIR = PROJECT_ROOT / "xml"

# 3. 定义输出路径：强烈建议把生成的数据集中放在一个目录里（比如 data/）
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)  # 如果 data 文件夹不存在，自动创建
OUT = DATA_DIR / "data_dump.jsonl"

# 支持两种文件名：dump_001.xml 或 Minecraft+Wiki-*.xml
def list_inputs() -> list[Path]:
    # 改为从 XML_DIR 进行 glob 搜索
    files = sorted(XML_DIR.glob("dump_*.xml"))
    if files:
        return files
    files = sorted(XML_DIR.glob("Minecraft+Wiki-*.xml"))
    return files

def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag

def detect_ns(root_tag: str) -> str:
    # root_tag like "{http://www.mediawiki.org/xml/export-0.11/}mediawiki"
    if root_tag.startswith("{") and "}" in root_tag:
        return root_tag.split("}", 1)[0] + "}"
    return ""

_HEADING_RE = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$")

def normalize_wikitext(text: str) -> str:
    """
    轻度清洗：减少“奇怪空格/不可见字符”对后续解析与检索的影响。
    不做激进改写，避免破坏模板/语法。
    """
    if not text:
        return text
    # 常见：全角空格、不可见空格
    text = text.replace("\u3000", " ")
    text = text.replace("\u00A0", " ")
    # 常见：人为插入空格导致 token 断裂（例如“交 易:”）
    text = text.replace("交 易", "交易")
    return text

def split_sections_from_wikitext(wikitext: str, keep_empty_intro: bool = False) -> list[dict]:
    """
    将 wikitext 按 = Heading = 拆分为分段结构，并补充层级 path：
    - title: 本节标题
    - parent: 父节标题（None 表示顶层）
    - path: 从顶层到本节的“/”路径，例如 "村民/交易"
    - section_index: 在本页面 sections 中的顺序编号（从 0 开始）
    """
    lines = wikitext.splitlines()
    sections: list[dict] = []

    current_title = "导言"
    current_level = 1
    current_parent = None
    current_path = "导言"
    buf: list[str] = []

    # 标题栈：(title, level)
    stack: list[tuple[str, int]] = []
    # 路径栈（与 stack 同步），存每层标题名
    path_stack: list[str] = []

    def flush_if_needed(force: bool = False):
        nonlocal buf, current_title, current_level, current_parent, current_path
        text = "\n".join(buf).strip("\n")
        if force or text or (keep_empty_intro and current_title == "导言"):
            sections.append(
                {
                    "title": current_title,
                    "level": current_level,
                    "parent": current_parent,
                    "path": current_path,
                    "text": text,
                    "section_index": len(sections),
                }
            )
        buf = []

    for ln in lines:
        m = _HEADING_RE.match(ln)
        if m:
            # 碰到新标题：先把上一段 flush
            flush_if_needed(force=False)

            level = len(m.group(1))
            title = m.group(2).strip()

            # 弹出同级或更深
            while stack and stack[-1][1] >= level:
                stack.pop()
                if path_stack:
                    path_stack.pop()

            parent = stack[-1][0] if stack else None

            # 更新当前 section 元信息
            current_title = title
            current_level = level
            current_parent = parent

            # 构造 path：顶层用 title；非顶层拼接父路径
            # path_stack 此时代表父链（不含自己）
            if path_stack:
                current_path = "/".join(path_stack + [title])
            else:
                current_path = title

            # 入栈
            stack.append((title, level))
            path_stack.append(title)
            continue

        buf.append(ln)

    # 文件结束：最后一段强制 flush
    flush_if_needed(force=True)
    return sections

def parse_one(xml_path: Path, out_f, *, skip_redirect: bool = True) -> int:
    """
    解析单个 MediaWiki export XML，输出 jsonl。
    重点：即便 XML 尾部轻微不完整导致 ParseError，也尽量保留已解析 pages。
    """
    ctx = ET.iterparse(str(xml_path), events=("start", "end"))
    try:
        event, root = next(ctx)  # start root
    except StopIteration:
        return 0

    ns = detect_ns(root.tag)

    n = 0
    page_idx = 0

    try:
        for event, elem in ctx:
            if event != "end":
                continue
            if strip_ns(elem.tag) != "page":
                continue

            page_idx += 1

            title = elem.findtext(f"{ns}title") or elem.findtext("title")
            if not title:
                elem.clear()
                continue

            # 可选：跳过重定向页（减少无意义数据）
            if skip_redirect:
                redir = elem.find(f"{ns}redirect") if ns else elem.find("redirect")
                if redir is not None:
                    elem.clear()
                    continue

            rev = elem.find(f"{ns}revision") if ns else None
            if rev is None:
                rev = elem.find("revision")

            text = None
            if rev is not None:
                text = rev.findtext(f"{ns}text") or rev.findtext("text")

            if text is not None:
                text = normalize_wikitext(text)
                sections = split_sections_from_wikitext(text)

                rec = {
                    "title": title,
                    "url": f"https://zh.minecraft.wiki/wiki/{quote(title)}",
                    "wikitext": text,
                    "sections": sections,
                    # 方便排查与定位
                    "source_xml": xml_path.name,
                    "page_index_in_file": page_idx,
                }
                out_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1

            # 清理已处理节点，降低内存
            elem.clear()
            # 进一步释放 root 下已处理的子节点（iterparse 减内存）
            while root is not None and len(root) > 0:
                # 不依赖 namespace，直接判断尾部 tag 是否为 page
                if strip_ns(root[0].tag) == "page":
                    del root[0]
                else:
                    break

    except ET.ParseError as e:
        # ✅ 关键：保留已写出的内容，提示后返回
        print(
            f"  ⚠️ ParseError(尾部/截断)：{e}\n"
            f"     已输出 pages={n}（文件：{xml_path.name}，最后处理到 page_index={page_idx}）"
        )
        return n

    return n

def main():
    inputs = list_inputs()
    if not inputs:
        print("❌ 没找到 dump_*.xml 或 Minecraft+Wiki-*.xml")
        return

    print(f"输入文件数: {len(inputs)}")
    total = 0

    with OUT.open("w", encoding="utf-8", newline="\n") as out_f:
        for i, p in enumerate(inputs, start=1):
            print(f"[{i}/{len(inputs)}] 解析: {p.name}")
            try:
                got = parse_one(p, out_f, skip_redirect=True)
                print(f"  ✅ pages={got}")
                total += got
            except Exception as e:
                # 兜底：不让单个文件把总任务打断
                print(f"  ❌ Error: {e} （文件：{p.name}）")

    print(f"\n✅ 输出完成: {OUT} total_pages={total}")

if __name__ == "__main__":
    main()