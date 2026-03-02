# MineRAG
![LOGO](tauri-app/src/public/backround.png)

MineRAG 是一个面向 Minecraft 中文 Wiki 的本地 RAG 问答工具。

它的检索、向量索引和 Embedding 都在本地完成，问答生成通过 DeepSeek API 完成。项目同时提供：

- Tauri 桌面前端
- FastAPI Python 后端
- 命令行问答入口
- 数据清洗、切块、建索引脚本

## 功能概览

- 基于 `FAISS` 的本地向量检索
- 使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 生成向量
- 使用 DeepSeek 进行最终回答生成
- 支持证据引用、调试信息、token 与成本估算
- 提供桌面端界面和命令行两种使用方式

## 全过程流程图

![Whole_Process](Whole_Process.png)

## RAG流程图

![RAG](RAG.png)

## 仓库结构


```text
.
├─ backend/
│  ├─ backend.py
│  └─ rag_cli.py
├─ data_pipeline/
│  ├─ 01get_titles_parsed.py
│  ├─ 02parsedtochunk.py
│  └─ 03buildindex.py
├─ pyserver/
│  ├─ requirements.txt
│  └─ server.py
├─ tauri-app/
│  ├─ package.json
│  ├─ src/
│  └─ src-tauri/
├─ config.py
├─ logging_utils.py
├─ build.ps1
└─ Readme.md
```

说明：

- `backend/`：核心问答与检索逻辑。
- `pyserver/`：FastAPI 服务，供 Tauri 前端调用。
- `data_pipeline/`：Wiki 数据解析、切块、建索引流程。
- `tauri-app/`：桌面端前端与 Tauri 壳。
- `build.ps1`：给自行编译用户的一键构建脚本。

## 运行方式

### 环境要求

- Windows
- Python 3.10+
- Node.js 18+
- Rust / Cargo

### 安装依赖

安装 Python 依赖：

```powershell
pip install -r pyserver/requirements.txt
```

安装前端依赖：

```powershell
cd tauri-app
npm install
```

### 启动开发版桌面端

```powershell
cd tauri-app
npm run tauri dev
```

开发模式下，Tauri 会从源码目录启动 `pyserver/server.py`。

## 自行编译

仓库根目录提供了自动构建脚本 [build.ps1](/c:/minecraft-ass/source/build.ps1)。

它会自动完成以下步骤：

1. 检查 `Python` 和 `npm` 是否可用
2. 安装 `pyserver/requirements.txt` 中的 Python 依赖
3. 运行数据构建脚本
4. 在 `tauri-app` 下执行 `npm run tauri build`
5. 将生成的 `MineRAG.exe` 复制到仓库根目录

运行方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

如果系统中没有 Python，脚本会直接提示并退出。

## 数据构建流程

当前仓库中的数据流程由以下 3 个脚本组成：

```text
data_pipeline/01get_titles_parsed.py
data_pipeline/02parsedtochunk.py
data_pipeline/03buildindex.py
```

它们的职责分别是：

1. `01get_titles_parsed.py`
   从 Minecraft 中文 Wiki 拉取页面内容，生成解析后的 `jsonl` 数据。
2. `02parsedtochunk.py`
   将解析后的页面切分为适合检索的 chunks。
3. `03buildindex.py`
   对 chunks 生成 embedding，并构建 FAISS 索引与元数据文件。

最终会生成的核心产物通常包括：

```text
data/data_parsed.jsonl
chunks/chunks_all.jsonl
index/faiss_all.index
index/meta_all.jsonl
```

说明：

- `01get_titles_parsed.py` 需要联网访问 Minecraft 中文 Wiki API。
- `03buildindex.py` 首次运行可能会下载 Hugging Face 模型，耗时较长。

## 桌面端构建结果

Tauri 当前产品名为 `MineRAG`，执行以下命令后：

```powershell
cd tauri-app
npm run tauri build
```

Windows 可执行文件会生成到：

```text
tauri-app/src-tauri/target/release/MineRAG.exe
```

## 配置说明

运行时配置由 [config.py](/c:/minecraft-ass/source/config.py) 管理。

默认配置项包括：

- `api_base`
- `model`
- `cache_hit_rate`
- `input_hit_per_million`
- `input_miss_per_million`
- `output_per_million`
- `font_size`
- `debug_mode`
- `log_level`

补充说明：

- `api_key` 默认不落盘保存。
- 运行时可以通过前端输入 API Key，或通过命令行参数 / 环境变量传入。
- 环境变量 `DEEPSEEK_API_KEY` 或 `API_KEY` 会覆盖默认空值。

## 数据目录与日志

项目运行时依赖环境变量 `MWA_DATA_DIR` 指定数据目录。

桌面端启动 Python 后端时，会自动把 Tauri 的 `app_data_dir` 注入为 `MWA_DATA_DIR`，用于统一保存：

```text
config.json
logs/
```

日志目录由 [logging_utils.py](/c:/minecraft-ass/source/logging_utils.py) 和 [config.py](/c:/minecraft-ass/source/config.py) 统一处理。

常见日志文件：

```text
logs/server.log
logs/fatal.log
```

## 技术栈

- Tauri 2
- Vite
- FastAPI
- FAISS CPU
- sentence-transformers
- transformers
- PyTorch CPU
- DeepSeek API