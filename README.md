# AI Vulnerability Scanner (AVS)

AI 驱动的自动化漏洞挖掘系统，支持 Web UI 和 CLI 两种模式。

## 架构

```
avs (CLI) --> Engine --> DeepSeek API
                  |
Web UI --> FastAPI --> SQLite
                  |
            WebSocket --> 实时输出
```

- **后端**: FastAPI + Uvicorn
- **前端**: Vue 3 SPA (无构建)
- **AI**: DeepSeek v4-pro
- **数据库**: SQLite (aiosqlite)
- **实时**: WebSocket 推送扫描进度

## 快速开始

### 环境要求

- Python >= 3.12
- DeepSeek API Key

### 安装

```bash
git clone https://github.com/lsr365400/ai-vuln-system.git
cd ai-vuln-system

# 安装依赖
pip install -e .

# 配置
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

### 配置项

```bash
# .env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro

# Web 登录密码 (Web UI 必填)
AUTH_PASSWORD=your-password
AUTH_SECRET=random-string
```

### CLI 模式

```bash
# 基础扫描
python cli/avs.py https://target.edu.cn

# 指定场景和优先级
python cli/avs.py https://target.edu.cn -s edu -p 10

# 自定义项目
python cli/avs.py https://target.edu.cn -s custom --project my-project
```

### Web UI 模式

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

浏览器打开 `http://localhost:8080`，输入 `.env` 中配置的密码登录。

## 功能

| 模块 | 说明 |
|------|------|
| 仪表盘 | 实时概览运行中/排队中会话、漏洞统计 |
| 会话管理 | 创建/停止/删除扫描会话 |
| 实时监控 | WebSocket 推送 AI 思考过程、工具调用 |
| 漏洞报告 | 按严重程度过滤，查看详细报告 |
| 多场景 | custom / edu / src 三种预设场景 |

## 项目结构

```
├── cli/                  # CLI 入口
│   └── avs.py
├── frontend/             # Web 前端
│   ├── index.html        # SPA 主页面
│   └── login.html        # 登录页
├── skills/               # AI Skill 定义
│   ├── core-skill.md     # 渗透测试核心约束
│   ├── edu-data-collector.md
│   └── session.py
├── scenarios/            # 场景规则
│   └── edu-rules.md
├── src/
│   ├── main.py           # FastAPI 入口
│   ├── auth.py           # 登录认证中间件
│   ├── config.py         # 配置管理
│   ├── database.py       # 数据库初始化
│   ├── scheduler.py      # 会话调度器 (max 5 concurrent)
│   ├── api/              # API 路由
│   │   ├── routes_sessions.py
│   │   ├── routes_reports.py
│   │   └── websocket_handler.py
│   └── engine/           # 扫描引擎
│       ├── session.py        # 会话生命周期
│       ├── deepseek_client.py # DeepSeek API 封装
│       ├── tool_executor.py  # 工具执行器
│       ├── prompt_builder.py # 提示词构建
│       ├── compressor.py     # 对话压缩
│       └── memory/           # 记忆管理
└── pyproject.toml
```

## 安全

- Web UI 带有登录认证 (HMAC-signed session cookie)
- `.env` 已加入 `.gitignore`，API Key 不会提交
- 会话超时自动中止 (默认 4 小时)
- 磁盘占用保护 (默认 5GB 上限)
- 最大轮数限制 (默认 200 轮)
