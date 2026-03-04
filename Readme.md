# MineRAG 

[123网盘下载](https://www.123865.com/s/dR9STd-JvJD3?pwd=RAE3#)


![LOGO](tauri-app/src/public/backround.png)

MineRAG 是一个面向 Minecraft 中文 Wiki 的本地 RAG 问答工具。

如果您喜欢本项目，请给我star。这是我持续开发的最大动力。


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

## 后端RAG流程图

![RAG](RAG.png)

## data_pipeline流程图

![data_pipeline](data_pipeline.png)

## 运行方式

### 环境要求

- Windows
- Python 3.10+
- Node.js 18+
- Rust / Cargo

注意：本项目的 Python 依赖包含 `sentence-transformers`、`torch`、`faiss-cpu` 等包，实际要求也是 Python 3.10 或更高版本。Python 3.9 及以下即使部分脚本能运行，也无法完整安装和运行整个项目。

可以先执行下面的命令确认版本：

```powershell
python --version
```

或：

```powershell
py -3 --version
```

### 安装依赖

安装 Python 依赖：

```powershell
pip install -r pyserver/requirements.txt
```

如果版本低于 Python 3.10，请先升级 Python，再安装依赖。

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
2. 检查 Python 版本是否至少为 `3.10`
3. 安装 `pyserver/requirements.txt` 中的 Python 依赖
4. 运行数据构建脚本
5. 在 `tauri-app` 下执行 `npm run tauri build`
6. 将生成的 `MineRAG.exe` 复制到仓库根目录

运行方式：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

如果系统中没有 Python，或 Python 版本低于 3.10，脚本会直接提示并退出。

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
