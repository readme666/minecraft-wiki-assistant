import json
import os
import sys
from pathlib import Path

import requests
from huggingface_hub import HfApi, hf_hub_url

MODEL_REPO_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_DIR_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
MODEL_FILES = [
    ".gitattributes",
    "1_Pooling/config.json",
    "README.md",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "unigram.json",
]

ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "pyserver" / "models"
MODEL_DIR = MODELS_DIR / MODEL_DIR_NAME
HF_CACHE_DIR = ROOT_DIR / ".hf_cache"
CHUNK_SIZE = 256 * 1024

os.environ.setdefault("HF_HOME", str(HF_CACHE_DIR))
os.environ.setdefault("TRANSFORMERS_CACHE", str(HF_CACHE_DIR / "transformers"))


def emit(event: str, **payload) -> None:
    msg = {"event": event, **payload}
    print(f"MODEL_DOWNLOAD {json.dumps(msg, ensure_ascii=False)}", flush=True)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def load_file_sizes() -> dict[str, int]:
    info = HfApi().model_info(MODEL_REPO_ID, files_metadata=True)
    sizes: dict[str, int] = {}
    for sibling in info.siblings:
        name = getattr(sibling, "rfilename", None)
        if not name:
            continue
        sizes[name] = int(getattr(sibling, "size", 0) or 0)
    return sizes


def download_file(
    session: requests.Session,
    file_name: str,
    target_path: Path,
    completed_bytes: int,
    total_bytes: int,
) -> None:
    temp_path = target_path.with_suffix(target_path.suffix + ".part")
    url = hf_hub_url(MODEL_REPO_ID, file_name)

    with session.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        downloaded = completed_bytes
        with temp_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                emit(
                    "progress",
                    file=file_name,
                    downloaded_bytes=downloaded,
                    total_bytes=total_bytes,
                )

    temp_path.replace(target_path)


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    file_size_map = load_file_sizes()
    total_bytes = 0
    completed_bytes = 0

    emit("status", message="正在检查模型文件...")
    for file_name in MODEL_FILES:
        file_size = file_size_map.get(file_name, 0)
        total_bytes += file_size
        if (MODEL_DIR / file_name).exists():
            completed_bytes += file_size

    emit(
        "meta",
        total_bytes=total_bytes,
        downloaded_bytes=completed_bytes,
        file_count=len(MODEL_FILES),
    )

    session = requests.Session()
    session.headers.update({"User-Agent": "MineRAG/1.0"})

    for file_name in MODEL_FILES:
        file_size = file_size_map.get(file_name, 0)
        target_path = MODEL_DIR / file_name
        ensure_parent(target_path)

        if target_path.exists():
            emit("status", message=f"正在准备 {file_name} ...")
            emit(
                "progress",
                file=file_name,
                downloaded_bytes=completed_bytes,
                total_bytes=total_bytes,
            )
            continue

        emit("status", message=f"正在下载 {file_name} ...")
        download_file(
            session=session,
            file_name=file_name,
            target_path=target_path,
            completed_bytes=completed_bytes,
            total_bytes=total_bytes,
        )
        completed_bytes += file_size
        emit(
            "progress",
            file=file_name,
            downloaded_bytes=completed_bytes,
            total_bytes=total_bytes,
        )

    emit("done", model_dir=str(MODEL_DIR), total_bytes=total_bytes)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        emit("error", error=format_error(exc))
        raise
