# 00_collect_allpages.py
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

API = "https://zh.minecraft.wiki/api.php"
OUT = Path("titles_all.txt")

# 主空间 = 0；如果你之后还想抓 Category 等，可改 apnamespace
NAMESPACE = 0

# 是否跳过重定向（强烈建议 True：减少重复与噪声）
SKIP_REDIRECTS = True

# 节流与重试
SLEEP = 0.2
RETRIES = 5
BACKOFF = 1.6
TIMEOUT = 60

UA = "minecraft-assistant/0.1 (allpages collector; contact: local-script)"


def _get_json(session: requests.Session, params: Dict) -> Dict:
    last: Optional[Exception] = None
    for i in range(1, RETRIES + 1):
        try:
            r = session.get(API, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            wait = (BACKOFF ** (i - 1)) * SLEEP
            print(f"⚠️ 请求失败 {i}/{RETRIES}: {e} -> {wait:.2f}s 后重试")
            time.sleep(wait)
    raise RuntimeError(f"请求多次失败: {last}")


def main():
    print(f"API: {API}")
    print(f"namespace={NAMESPACE} skip_redirects={SKIP_REDIRECTS}")
    print(f"sleep={SLEEP} retries={RETRIES} backoff={BACKOFF}")
    print("-" * 60)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.3",
        }
    )

    titles: List[str] = []
    seen = set()

    apcontinue = None
    page = 0
    while True:
        params = {
            "action": "query",
            "format": "json",
            "list": "allpages",
            "apnamespace": str(NAMESPACE),
            "aplimit": "max",
        }
        if SKIP_REDIRECTS:
            params["apfilterredir"] = "nonredirects"
        if apcontinue:
            params["apcontinue"] = apcontinue

        data = _get_json(session, params)
        items = data.get("query", {}).get("allpages", [])
        page += 1

        added = 0
        for it in items:
            t = (it.get("title") or "").strip()
            if not t:
                continue
            if t not in seen:
                seen.add(t)
                titles.append(t)
                added += 1

        apcontinue = data.get("continue", {}).get("apcontinue")
        print(f"page={page:4d} got={len(items):4d} added={added:4d} total={len(titles):6d}")

        time.sleep(SLEEP)

        if not apcontinue:
            break

    # 输出
    OUT.write_text("\n".join(titles) + "\n", encoding="utf-8", newline="\n")
    print("-" * 60)
    print(f"✅ 完成：titles={len(titles)} -> {OUT.resolve()}")


if __name__ == "__main__":
    # 建议用：python -X utf8 00_collect_allpages.py
    main()