# AI 漏洞挖掘系统 (AI Vulnerability Scanner)

AI 驱动的自动化渗透测试平台。将安全测试的决策逻辑沉淀为结构化提示词规则，通过 Function Calling 调度 17 个安全工具，实现从信息收集到漏洞报告的全自主测试链路。

---

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                  Frontend (Vue 3 SPA)                    │
│                   HTTP + WebSocket                       │
├─────────────────────────────────────────────────────────┤
│               FastAPI (src/main.py)                     │
│  Auth Middleware  │  REST API  │  WebSocket 实时推送    │
├─────────────────────────────────────────────────────────┤
│          Scheduler (优先级队列 + 5并发限流)              │
├─────────────────────────────────────────────────────────┤
│                    Scan Engine                           │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Session Runner (会话循环, 最多200轮)              │  │
│  │  ├─ Prompt Builder (core-skill.md + 记忆卡)       │  │
│  │  ├─ DeepSeekClient (OpenAI-compatible 流式调用)   │  │
│  │  ├─ Tool Executor (17个工具, 安全拦截)            │  │
│  │  ├─ Context Compressor (80%阈值, 压缩后重注入)    │  │
│  │  └─ Cross-Domain Scope (通配符跨域 + 硬拦截)      │  │
│  └──────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│                   Memory System                          │
│  ┌────────────────────┐  ┌──────────────────────────┐  │
│  │ PentAGI (向量记忆)  │  │ Hermes Store (文件记忆)  │  │
│  │ SiliconFlow-BGE │  │ data/memory/*.md + 索引   │  │
│  │ 1024维 + 余弦搜索  │  │ 目标画像/发现/进度/误报  │  │
│  └────────────────────┘  └──────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│              SQLite + File System                        │
│  sessions / reports / endpoints / attack_chains          │
│  vector_memory / failed_paths / technique_effectiveness  │
└─────────────────────────────────────────────────────────┘
```

---

## 启动

### 生产部署 (当前云服务器运行方式)

```bash
# SSH 到服务器
ssh ai-scanner

# 进入项目目录
cd /home/ubuntu/ai-vuln-system

# 后台启动
nohup python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8080 > /tmp/ai-vuln.log 2>&1 &

# 验证
curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/
# 返回 307 (重定向到登录页) 即正常
```

### 本地开发

```bash
git clone https://github.com/lsr365400/ai-vuln-system.git
cd ai-vuln-system

# 配置
cp .env.example .env
# 编辑 .env，填入 API Key

# 安装依赖
pip install fastapi uvicorn aiosqlite httpx openai pydantic-settings pyyaml

# 启动
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

浏览器打开 `http://localhost:8080`，输入 `.env` 中 `AUTH_PASSWORD` 配置的密码登录。

---

## 配置

```bash
# DeepSeek API
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

# SiliconFlow Embedding (向量记忆)
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_API_URL=https://api.siliconflow.cn/v1/embeddings
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5

# Auth
AUTH_PASSWORD=your-password
AUTH_SECRET=random-string

# 安全约束
SESSION_DISK_LIMIT_GB=5      # 磁盘配额
SESSION_MAX_TURNS=200         # 最大轮数
SESSION_TIMEOUT_HOURS=4       # 硬超时
```

---

## 核心设计

### core-skill.md — AI 的行为约束文件

277 行结构化提示词，遵循六大设计准则：

| 准则 | 说明 |
|------|------|
| 垃圾洞清单置顶 | 14 类不报项（CORS/SourceMap/API文档/盲SSRF/靶场等），AI 第一眼看到 |
| 灵魂金句 | "现象不是漏洞，漏洞是结果"——决定什么报、什么不报 |
| 速查卡防遗忘 | 27 条核心规则（测试节奏 + 质量底线 + 绝不能做），换方向时重读 |
| 七问验证门 | 写报告前逐条自查，第七问"漏洞平台会接受吗"是终极过滤 |
| 决策树 | 按登录态/技术栈/功能分支，非固定流程 |
| 知识下沉 | 不写具体 payload，AI 自己知道怎么注 |

### 17 个 Function Calling 工具

| 类别 | 工具 | 用途 |
|------|------|------|
| 浏览器 | browser_navigate/login/extract | Playwright 自动化登录/提取 |
| HTTP | curl_http/discover_endpoints | 请求探测/端点爬取 |
| JS分析 | analyze_js/analyze_sourcemap | jsluice 路由提取/SourceMap 解析 |
| 认证 | check_auth | 登录状态验证 |
| 利用 | exec_shell/brute_force | 命令执行(沙箱)/字典爆破 |
| 报告 | write_report | 漏洞报告输出 |
| 记忆 | search_memory/check_failed_paths/search_experience/save_memory | 跨会话情报持久化 |
| 控制 | finish_session | 会话终止 |

### 三层记忆系统

| 层 | 实现 | 用途 |
|----|------|------|
| 向量记忆 | SiliconFlow BGE-large-zh 1024维 + 余弦相似 ≥0.75 | 跨目标语义搜索 |
| 文件记忆 | data/memory/*.md + MEMORY.md 索引 | 目标画像/发现/进度/误报 |
| 上下文压缩 | Token 估算, 80% 阈值, DeepSeek 摘要 | 长会话防溢出, 压缩后重注入 memory + core-skill |

### 运行时交互

- **通配符跨域**: 输入 `*.edu.cn` 自动扩正则为匹配模式, 工具层硬拦截范围外 URL
- **打断注入**: 运行中可随时发送指令调整 AI 方向, 通过内存队列推送到会话循环
- **NEED_INPUT 暂停**: AI 受阻时主动暂停等待人工引导, 不终止会话

---

## 功能

| 模块 | 说明 |
|------|------|
| 仪表盘 | 实时概览运行中/排队中会话、漏洞统计 |
| 会话管理 | 输入初始指令创建/打断/停止/删除会话 |
| 实时监控 | WebSocket 推送 AI 思考过程、工具调用 |
| 漏洞报告 | 按严重程度过滤，查看详细报告 |
| 多场景 | custom / edu / src 预设场景规则 |

---

## 项目结构

```
├── frontend/                 # Vue 3 SPA (无构建)
│   ├── index.html            # 主页面
│   └── login.html            # 登录页
├── skills/                   # AI 行为规则
│   ├── core-skill.md         # 277行核心约束 (垃圾清单/速查卡/铁律/七问门/决策树)
│   └── session.py            # CLI 会话入口
├── scenarios/                # 场景规则文件
├── src/
│   ├── main.py               # FastAPI 入口 + 生命周期
│   ├── auth.py               # HMAC Cookie 认证中间件
│   ├── config.py             # Pydantic Settings 配置
│   ├── database.py           # SQLite 初始化 + CRUD
│   ├── scheduler.py          # 优先级队列 + Semaphore 并发控制
│   ├── api/                  # REST + WebSocket
│   │   ├── routes_sessions.py  # 会话CRUD + prompt打断注入
│   │   ├── routes_reports.py   # 报告查询
│   │   └── websocket_handler.py
│   ├── engine/               # 扫描引擎核心
│   │   ├── session.py          # 会话生命周期 (650行)
│   │   ├── deepseek_client.py  # OpenAI SDK 封装 + TOOLS定义
│   │   ├── tool_executor.py    # 工具执行 + 安全拦截 + 域验证
│   │   ├── prompt_builder.py   # 系统提示词构建 + 通配符跨域
│   │   ├── browser_tool.py     # Playwright 浏览器自动化
│   │   ├── report_indexer.py   # 报告入库 + 邮件通知
│   │   └── memory/             # 三层记忆
│   │       ├── pentagi_memory.py   # 向量嵌入 + 攻击链
│   │       ├── hermes_store.py     # Markdown 文件记忆
│   │       └── compressor.py       # 上下文压缩
│   └── safety/
│       └── disk_guard.py      # 磁盘配额保护
├── data/                     # 运行时数据 (gitignored)
│   ├── db.sqlite3            # 数据库
│   ├── reports/              # 漏洞报告
│   ├── sessions/             # 会话临时文件
│   └── memory/               # Hermes 持久化记忆
├── tools/                    # 辅助脚本
├── tests/                    # 单元测试
├── cli/avs.py                # CLI 入口
├── .env.example              # 配置模板
└── pyproject.toml
```

---

## 安全约束

- **工具层**: 命令黑名单 (rm -rf / mkfs / shutdown / 反弹Shell) + 文件操作沙箱隔离
- **域验证**: 跨域模式下硬拦截范围外 URL (正则匹配)
- **应用层**: HMAC Cookie 认证, 会话超时 (4h), 磁盘配额 (5GB), 最大轮数 (200)
- **提示词层**: 铁律 (禁止改用户数据/禁止内网扫描/禁止DDoS), RCE whoami 即止
