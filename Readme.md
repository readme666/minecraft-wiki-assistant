# 🧠 Minecraft Wiki 助手

> 基于本地向量检索（FAISS）+ 本地 Embedding 模型 + DeepSeek 大模型的桌面级 RAG 问答系统。

一个面向 Minecraft 中文 Wiki 的智能问答助手，
通过本地语义检索与大模型推理，实现高质量、可追溯的回答生成。

---

# ✨ 项目特点

* 🔍 **本地语义检索（FAISS）**
* 📚 完整 Minecraft Wiki 向量索引
* 🧠 本地 Embedding 模型推理（无需联网）
* 🎯 自动义项消歧
* 🔐 API Key 默认仅内存保存（更安全）
* 🚀 基于 Tauri 构建的桌面应用
* 📦 内嵌 Python 运行环境（无需安装 Python）

---

# 🏗 技术架构

```
用户问题
    ↓
问题分类
    ↓
检索计划生成
    ↓
FAISS 向量检索（本地）
    ↓
证据整合
    ↓
DeepSeek LLM 生成最终回答
```

Embedding 模型：

```
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

向量推理完全在本地执行。

---

# 📦 Release 目录结构

```
Release/
│
├── minecraft-wiki-assistant.exe     # 主程序
│
├── python/                          # 内嵌 Python 运行环境
│
├── pyserver/
│   ├── server.py                    # FastAPI 后端
│   ├── models/                      # 本地 embedding 模型
│   └── requirements.txt
│
├── index/
│   ├── faiss_all.index              # 向量索引文件
│   └── meta_all.jsonl               # 元数据
│
├── rag_cli.py                       # RAG 核心逻辑
├── backend.py                       # GUI 调用入口
└── config.py                        # 配置系统
```

---

# 🚀 使用方法

## Windows

1. 下载 Release 包
2. 双击运行：

```
minecraft-wiki-assistant.exe
```

无需安装 Python。

---

# 🔑 API Key 配置

本程序需要使用 **DeepSeek API Key**。

* 在设置中填写 API Key
* 默认仅保存在内存中
* 不写入磁盘（除非手动修改代码）

若无法生成回答，请检查：

* API Key 是否正确
* 是否能访问 `https://api.deepseek.com`

---

# 🌐 网络需求说明

| 模块           | 是否需要联网 |
| ------------ | ------ |
| Embedding 模型 | ❌ 不需要  |
| 向量检索         | ❌ 不需要  |
| HuggingFace  | ❌ 不需要  |
| DeepSeek API | ✅ 需要   |

程序本身完全离线，仅在生成最终答案时调用 DeepSeek API。

---

# 📁 数据存储路径

Windows 默认：

```
C:\Users\<用户名>\AppData\Roaming\com.minecraft.wiki.assistant\
```

包含：

* logs/
* config.json（不含 api_key）

---

# 📝 日志说明

日志文件位置：

```
logs/server.log
```

如遇问题，请提供该文件内容。

---

# 🧩 技术栈

* FastAPI
* FAISS（CPU）
* Sentence-Transformers
* Transformers
* PyTorch（CPU）
* DeepSeek API
* Tauri（Rust + WebView）

---

# ⚙️ 性能说明

推荐配置：

* CPU：i5 / Ryzen 5 及以上
* 内存：16GB+
* 建议使用 SSD

响应时间参考：

| 设备      | 单次回答时间  |
| ------- | ------- |
| 桌面级 CPU | 30~60 秒 |
| 低功耗笔记本  | 1~4 分钟  |

首次启动会进行 warmup，可能较慢。

---

# 🔒 安全说明

* API Key 默认不落盘
* 不包含遥测
* 不进行 HuggingFace 在线下载
* 无后台常驻进程

---

# ⚠ 已知限制

* 目前仅支持 Windows
* CPU 推理，未启用 GPU
* 长问题响应时间较长
* 检索策略仍在优化中

---

# 🎯 后续规划

* [ ] 检索速度优化
* [ ] 并行化改进
* [ ] 更精细的义项消歧策略
* [ ] 可选 GPU 加速
* [ ] Linux / macOS 支持
* [ ] 插件化检索策略

---

# 📜 模型与许可证

Embedding 模型：

```
sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

许可证：Apache 2.0

请在二次分发时遵守相关许可证。

---

# 📌 当前版本

```
v0.1.0
```

---

# 💬 项目说明

本项目旨在探索：

> 在本地构建大规模知识库的语义检索系统，并结合远程大模型进行推理生成。

目标是在：

* 本地可控
* 语义准确
* 可解释
* 工程可部署

之间取得平衡。

---

# 🤝 欢迎反馈

如有问题或建议，请提交 Issue，并附：

* 系统配置
* 日志文件
* 示例问题
* 期望行为

---

---
