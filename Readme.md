# Minecraft Wiki Assistant

一个面向 Minecraft 中文 Wiki 的桌面 RAG 问答工具。  
项目使用本地向量检索定位 Wiki 证据，再调用 DeepSeek 生成最终回答。检索、索引和 Embedding 都在本地完成，联网部分只用于调用大模型接口。

## 功能概览

- 基于 `FAISS` 的本地向量检索
- 本地 `Sentence-Transformers` Embedding 模型
- `DeepSeek` 生成最终回答
- 桌面端基于 `Tauri`
- 后端服务基于 `FastAPI`
- 支持证据引用、会话历史、成本估算、日志查看
- 支持命令行调用 `backend/rag_cli.py`

## 当前架构

```text
用户问题
  -> 问题分类 / 查询改写
  -> 本地 FAISS 检索
  -> 证据后处理与扩展
  -> DeepSeek 生成回答
  -> 返回答案 + 证据 + token / cost 信息
```

核心本地模型：

```text
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

## 项目结构

```text
source/
├── backend/
│   ├── backend.py
│   └── rag_cli.py
├── pyserver/
│   ├── server.py
│   └── requirements.txt
├── tauri-app/
│   ├── src/
│   └── src-tauri/
├── data_pipeline/
├── index/
├── chunks/
├── data/
├── titles/
├── xml/
├── config.py
└── minecraft-wiki-assistant.exe
```

## 运行方式

### 1. 直接运行已打包版本

仓库根目录已经包含：

```powershell
.\minecraft-wiki-assistant.exe
```

桌面程序启动后会拉起后端。
如果项目目录下存在内置 `python/` 运行时，会优先使用它；如果不存在，则会自动回退到系统默认 Python 环境。

### 2. 本地开发运行

前提：

- Python 3.10+
- Node.js 18+
- Rust / Cargo
- 已准备好 `index/faiss_all.index` 和 `index/meta_all.jsonl`

安装 Python 依赖：

```powershell
pip install -r pyserver/requirements.txt
```

如果你是从源码编译并运行项目，而且使用的是本机 Python（而不是项目内置的 `python/` 目录），则必须先按 [pyserver/requirements.txt](/c:/minecraft-ass/source/pyserver/requirements.txt) 安装这些依赖。

启动前端开发环境：

```powershell
cd tauri-app
npm install
npm run tauri dev
```

### 3. 单独运行命令行问答

```powershell
python backend/rag_cli.py --question "村民会卖什么" --api-key "<YOUR_DEEPSEEK_KEY>"
```

或进入交互模式：

```powershell
python backend/rag_cli.py --interactive --api-key "<YOUR_DEEPSEEK_KEY>"
```

## 配置说明

应用实际会使用两类配置：

- 后端配置文件：`config.json`
- 前端本地缓存：浏览器 `localStorage`

其中：

- `api_key` 默认不会写入后端 `config.json`
- 桌面端设置页里填写的 API Key 会保存在前端本地存储，用于后续请求时传给后端
- 其余设置如 `api_base`、`model`、`font_size`、`debug_mode` 会写入配置文件

默认配置项可在 [config.py](/c:/minecraft-ass/source/config.py) 中查看，包括：

- `api_base`
- `model`
- `cache_hit_rate`
- `input_hit_per_million`
- `input_miss_per_million`
- `output_per_million`
- `font_size`
- `debug_mode`

## 数据目录

运行时配置和日志目录由环境变量 `MWA_DATA_DIR` 决定。  
在桌面版中，这个路径由 Tauri 的 `app_data_dir` 注入，通常位于应用数据目录下，包含：

```text
config.json
logs/
```

## 日志

后端日志位于：

```text
logs/server.log
```

如果后端启动失败，还可能出现：

```text
logs/fatal.log
```

## 数据构建流程

如果你需要从 Wiki 原始数据重建索引，流程对应 `data_pipeline/` 下的脚本：

```text
00collect_allpages.py   获取页面标题
01xmltojson.py          将 XML dump 转为 JSONL
02dumptoparsed.py       展开模板并解析页面
03parsedtochunk.py      切分 chunk
04buildindex.py         生成向量索引与元数据
```

最终产物是：

```text
index/faiss_all.index
index/meta_all.jsonl
```

说明：

- `00collect_allpages.py`、`02dumptoparsed.py` 需要访问 Minecraft Wiki API
- `04buildindex.py` 会加载本地或 Hugging Face 模型来生成 Embedding

## 技术栈

- Tauri 2
- Vite
- FastAPI
- FAISS CPU
- Sentence-Transformers
- Transformers
- PyTorch CPU
- DeepSeek API

## 已知限制

- 当前主要面向 Windows
- 默认是 CPU 推理，首次启动会有 warmup
- 回答质量依赖本地索引和 DeepSeek 输出
- 如果缺少 `index` 文件，后端无法完成检索
- 若未配置 API Key，只能启动界面，无法生成最终回答

## 构建打包

仓库里已经有一个简单的打包脚本：

```powershell
.\test.bat
```

它会执行：

1. `tauri-app` 内的 `npm run tauri build`
2. 将生成的 EXE 复制到仓库根目录
3. 直接启动打包产物

## 版本

当前版本号在 [tauri-app/package.json](/c:/minecraft-ass/source/tauri-app/package.json) 和 [tauri-app/src-tauri/tauri.conf.json](/c:/minecraft-ass/source/tauri-app/src-tauri/tauri.conf.json) 中均为：

```text
0.1.0
```
