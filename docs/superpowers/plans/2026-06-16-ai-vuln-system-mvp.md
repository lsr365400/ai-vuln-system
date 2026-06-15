# AI 辅助漏洞挖掘系统 — MVP 阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 CLI 单会话 MVP：加载核心技能文件 → 调用 DeepSeek API → 自动执行渗透测试 → 产出漏洞报告

**Architecture:** 单进程 Python CLI，不依赖 FastAPI/WebSocket。核心组件：配置加载 → Prompt 构建 → DeepSeek 流式调用 → Tool 执行循环 → 报告保存。预留 engine/ 目录结构以便阶段二升级到 Web 平台。

**Tech Stack:** Python 3.12+, DeepSeek API (openai-compatible SDK), SQLite (aiosqlite), Pydantic v2, httpx (tool executor)

---

## 文件映射

| 文件 | 职责 | 行数估计 |
|------|------|---------|
| `pyproject.toml` | 项目元数据 + 依赖声明 | 30 |
| `.env.example` | 环境变量模板 | 8 |
| `src/__init__.py` | 包标记 | 1 |
| `src/config.py` | 从 .env 加载配置，Pydantic Settings | 30 |
| `src/database.py` | SQLite 初始化 + WAL + 建表 | 60 |
| `src/models.py` | Session/Report 数据模型 (Pydantic + dataclass) | 40 |
| `src/engine/__init__.py` | 包标记 | 1 |
| `src/engine/deepseek_client.py` | DeepSeek API 流式调用封装 | 60 |
| `src/engine/prompt_builder.py` | 组装 system prompt (核心技能文件 + 场景规则) | 40 |
| `src/engine/tool_executor.py` | Tool call 执行器 (curl/shell/write_report) | 80 |
| `src/engine/session.py` | 会话主循环 (构建 prompt → 轮次循环 → 终态判定) | 120 |
| `src/safety/__init__.py` | 包标记 | 1 |
| `src/safety/disk_guard.py` | 磁盘配额检测 + 自循环路径检测 | 50 |
| `skills/core-skill.md` | 核心技能文件 (按 9 区域结构) | ~180 |
| `scenarios/edu-rules.md` | 教育系统场景规则 (从现有 rules 迁移) | 30 |
| `cli/avs.py` | CLI 入口: argparse → 创建会话 → 运行 → 输出结果 | 60 |
| `tests/test_config.py` | 配置加载测试 | 25 |
| `tests/test_prompt_builder.py` | Prompt 构建测试 | 35 |
| `tests/test_disk_guard.py` | 磁盘防护测试 | 30 |
| `tests/test_session.py` | 集成测试 (需 DeepSeek API Key) | 50 |

---

### Task 1: 项目脚手架

**Files:**
- Create: `D:\desk\ai测试系统\pyproject.toml`
- Create: `D:\desk\ai测试系统\.env.example`
- Create: `D:\desk\ai测试系统\src\__init__.py`
- Create: `D:\desk\ai测试系统\src\engine\__init__.py`
- Create: `D:\desk\ai测试系统\src\safety\__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "ai-vuln-system"
version = "0.1.0"
description = "AI-assisted vulnerability mining system"
requires-python = ">=3.12"
dependencies = [
    "openai>=1.0.0",       # DeepSeek API (OpenAI-compatible)
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "aiosqlite>=0.20.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: 创建 .env.example**

```bash
# DeepSeek API
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

# 项目路径
PROJECT_ROOT=.
SKILL_FILE=skills/core-skill.md
REPORT_DIR=data/reports
SESSION_DIR=data/sessions

# 数据库
DATABASE_PATH=data/db.sqlite3

# 安全
SESSION_DISK_LIMIT_GB=5
SESSION_MAX_TURNS=200
SESSION_TIMEOUT_HOURS=4
```

- [ ] **Step 3: 创建空 __init__.py 文件**

```
# src/__init__.py, src/engine/__init__.py, src/safety/__init__.py — 均为空文件
```

Write each file:
- `src/__init__.py` — empty
- `src/engine/__init__.py` — empty
- `src/safety/__init__.py` — empty

- [ ] **Step 4: 创建目录结构**

Run: `mkdir -p "D:/desk/ai测试系统/skills" "D:/desk/ai测试系统/scenarios" "D:/desk/ai测试系统/data/sessions" "D:/desk/ai测试系统/data/reports" "D:/desk/ai测试系统/cli" "D:/desk/ai测试系统/tests"`

- [ ] **Step 5: 安装依赖**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pip install -e ".[dev]"`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example src/__init__.py src/engine/__init__.py src/safety/__init__.py
git commit -m "feat: project scaffold with dependencies"
```

---

### Task 2: 配置加载

**Files:**
- Create: `D:\desk\ai测试系统\src\config.py`
- Create: `D:\desk\ai测试系统\tests\test_config.py`

- [ ] **Step 1: 编写配置加载代码**

```python
# src/config.py
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek API
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # 项目路径
    project_root: Path = Path(".")
    skill_file: Path = Path("skills/core-skill.md")
    report_dir: Path = Path("data/reports")
    session_dir: Path = Path("data/sessions")

    # 数据库
    database_path: Path = Path("data/db.sqlite3")

    # 安全
    session_disk_limit_gb: int = 5
    session_max_turns: int = 200
    session_timeout_hours: int = 4

    def resolve_paths(self):
        """将相对路径转为基于 project_root 的绝对路径"""
        root = self.project_root.resolve()
        self.skill_file = root / self.skill_file
        self.report_dir = root / self.report_dir
        self.session_dir = root / self.session_dir
        self.database_path = root / self.database_path
        # 确保目录存在
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.resolve_paths()
    return s
```

- [ ] **Step 2: 编写配置测试**

```python
# tests/test_config.py
import os
from pathlib import Path
from unittest.mock import patch
from src.config import Settings, load_settings


def test_settings_defaults():
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True):
        s = Settings()
        assert s.deepseek_model == "deepseek-chat"
        assert s.session_max_turns == 200
        assert s.session_disk_limit_gb == 5


def test_resolve_paths(tmp_path):
    s = Settings(
        project_root=tmp_path,
        deepseek_api_key="test-key",
    )
    s.resolve_paths()
    assert s.report_dir.exists()
    assert s.session_dir.exists()
    assert s.database_path.parent.exists()
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_config.py -v`
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add configuration loading with pydantic-settings"
```

---

### Task 3: 数据库初始化

**Files:**
- Create: `D:\desk\ai测试系统\src\database.py`
- Create: `D:\desk\ai测试系统\src\models.py`

- [ ] **Step 1: 定义数据模型**

```python
# src/models.py
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class SessionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    VULN_FOUND = "vuln_found"
    LOW_ROI = "low_roi"
    NEED_INPUT = "need_input"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class Session:
    id: str
    project_id: str
    scenario: str
    target_url: str
    status: SessionStatus = SessionStatus.QUEUED
    priority: int = 5
    temp_dir: Optional[Path] = None
    report_dir: Optional[Path] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_msg: Optional[str] = None


@dataclass
class ReportMeta:
    session_id: str
    severity: str  # P1 / P2 / P3
    title: str
    target: str
    type: str
    file_path: Path
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
```

- [ ] **Step 2: 编写数据库初始化代码**

```python
# src/database.py
import aiosqlite
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    scenario        TEXT NOT NULL DEFAULT 'custom',
    target_url      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    priority        INTEGER NOT NULL DEFAULT 5,
    temp_dir        TEXT,
    report_dir      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    error_msg       TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    severity        TEXT NOT NULL,
    title           TEXT NOT NULL,
    target          TEXT NOT NULL,
    type            TEXT NOT NULL,
    fingerprint     TEXT UNIQUE,
    file_path       TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db(db_path: Path) -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(db_path))
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def insert_session(db: aiosqlite.Connection, s) -> None:
    await db.execute(
        """INSERT INTO sessions (id, project_id, scenario, target_url, status, priority, temp_dir, report_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (s.id, s.project_id, s.scenario, s.target_url, s.status.value,
         s.priority, str(s.temp_dir) if s.temp_dir else None,
         str(s.report_dir) if s.report_dir else None),
    )
    await db.commit()


async def update_session_status(db: aiosqlite.Connection, session_id: str, status: str,
                                 error_msg: str | None = None) -> None:
    if status in ("vuln_found", "low_roi", "need_input", "error", "stopped"):
        await db.execute(
            "UPDATE sessions SET status=?, finished_at=datetime('now'), error_msg=? WHERE id=?",
            (status, error_msg, session_id),
        )
    else:
        await db.execute(
            "UPDATE sessions SET status=?, error_msg=? WHERE id=?",
            (status, error_msg, session_id),
        )
    await db.commit()
```

- [ ] **Step 3: 验证数据库初始化**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -c "
import asyncio, aiosqlite, tempfile
from pathlib import Path
from src.database import init_db

async def main():
    db = await init_db(Path(tempfile.mkdtemp()) / 'test.db')
    cursor = await db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
    tables = [row[0] async for row in cursor]
    print('Tables:', tables)
    assert 'sessions' in tables
    assert 'reports' in tables
    assert 'event_log' in tables
    await db.close()
    print('OK')

asyncio.run(main())
"`
Expected: Tables: ['sessions', 'reports', 'event_log'] + OK

- [ ] **Step 4: Commit**

```bash
git add src/models.py src/database.py
git commit -m "feat: add data models and SQLite database setup"
```

---

### Task 4: 核心技能文件

**Files:**
- Create: `D:\desk\ai测试系统\skills\core-skill.md`

- [ ] **Step 1: 编写核心技能文件（按 9 区域结构）**

```markdown
# AI 渗透测试核心约束

## 垃圾洞清单 — 以下发现绝对不报

| 不报 | 原因 |
|------|------|
| CORS 跨域配置 | 跨域本身不是漏洞，除非证明窃取了具体数据 |
| Sourcemap 泄露 | 配置问题，不是可利用漏洞 |
| HTTP 安全头缺失 | X-Frame/CSP/HSTS 缺失是理论风险 |
| 版本号/中间件指纹 | 信息收集副产品，不是漏洞 |
| Self-XSS | 只能攻击自己的 XSS |
| SSL/TLS 配置警告 | 除非 POODLE/Heartbleed 级别 |
| 单独开放重定向 | 无法链式利用几乎无害 |
| Rate Limiting 缺失 | 功能建议 |
| 无 PoC 的发现 | 不能重现就不存在 |
| 备份文件/目录索引 | 除非内含敏感数据可证明 |

## 灵魂金句

> 现象不是漏洞，漏洞是结果。
> 报的是结果（越权/注入/RCE），不是过程（信息泄露/配置问题）。

## 速查卡 — 每次切换测试方向时重读

1. CORS ≠ 漏洞
2. 无 PoC ≠ 漏洞
3. 现象 ≠ 结果
4. Self-XSS = 垃圾
5. 安全头缺失 = 0元
6. Sourcemap = 0元
7. 20min 无进展 → 换方向
8. 30min 后 → 重读此卡
9. 报告必须有 curl/HTTP 请求
10. PoC 必须可重现
11. P3 以下不写报告
12. 禁止 rm -rf
13. 禁止反弹 Shell / 内网扫描
14. 禁止修改他人数据
15. 报的是结果，不是过程

## 铁律 — 绝对禁止的操作

- 禁止 `rm -rf` 或任何删除系统文件的操作
- 禁止反弹 Shell、挖矿、C2 植入
- 禁止内网端口扫描（SSRF 打 Metadata 除外）
- 禁止修改目标数据库/用户数据（只读测试）
- 禁止 DDoS / 暴力破解（单次验证除外）
- 写入文件只允许在临时目录内

## 七问验证门 — 写报告前逐条自答

| # | 问题 | 不通过则 |
|---|------|---------|
| 1 | 在授权范围内吗？ | 停止，不报 |
| 2 | 有完整 PoC（curl/HTTP）吗？ | 没有 → 不报 |
| 3 | 需要假设来解释危害吗？ | 需要 → 不报 |
| 4 | 影响是已证明的还是"可能"的？ | "可能" → 不报 |
| 5 | 是现象还是结果？ | 现象 → 不报 |
| 6 | 不懂安全的开发者能理解危害吗？ | 不能 → 写得更清楚 |
| 7 | 发到漏洞平台会被接受还是关闭？ | 会被关闭 → 不报 |

## 决策树 — 按目标特征动态选择测试方向

按以下优先级选择测试方向（不是固定流程）：

### 按登录态分支
- **有登录态**: 越权/IDOR → 水平→垂直→敏感数据→CSRF→Token abuse→业务逻辑
- **无登录态**: 信息泄露→未授权API→注入点→登录爆破→密码重置→注册逻辑

### 按技术栈分支
- **Java/Spring**: JNDI→反序列化→Actuator端点→SpEL注入→Thymeleaf SSTI
- **PHP**: LFI→SQL注入→文件上传→反序列化→SSRF
- **Node.js**: NoSQL注入→原型污染→SSJI→反序列化
- **Python**: SSTI→反序列化(pickle)→SQL注入→命令注入
- **Go**: 命令注入→路径穿越→CRLF→竞态
- **不明确时**: 从常见注入点开始探测，根据响应头/错误信息推断

### 按功能 ROI 分支
- **支付/充值**: 竞态条件→金额篡改→负数金额→重复提交→并发绕过
- **文件上传**: 扩展名绕过→MIME绕过→路径穿越→图片马→ZIP slip
- **数据导出**: SQL注入→越权导出→CSV/Excel公式注入→XXE
- **REST API**: BOLA→参数污染→Mass Assignment→批量操作越权
- **搜索/过滤**: SQL注入→XSS→命令注入→SSTI
- **认证/登录**: SQL注入→NoSQL注入→JWT伪造→OAuth劫持

### 时间约束
- **同一攻击面 >20min 无进展** → 必须换方向，不要死磕
- **换方向前** → 重读上方速查卡，确认没有遗忘核心规则
- **连续测试 >30min** → 暂停 1min，重读速查卡后继续

## 测试执行规范

- 每个请求输出完整的 curl 命令以便复现
- 发现可疑点先验证再深入，不要发散
- 测试时使用明显不存在的测试数据（如 test_user_nonexist_12345）
- 遇到 WAF/IPS 拦截不要放弃，尝试编码绕过
- 优先测高 ROI 功能，每个目标最多 4h

## 报告格式

用 write_report 工具输出报告时，严格按以下格式：

```markdown
---
severity: P1
title: [简短描述漏洞]
target: [目标URL或系统]
type: [sqli/idor/rce/ssrf/ssti/...]
date: [日期]
---
## 漏洞描述
[一句话说清楚是什么漏洞]

## 影响范围
[具体影响了什么数据/什么用户]

## 复现步骤
1. [步骤1]
2. [步骤2]
...

### PoC（可直接执行）
```bash
curl -s '...'
```

## 修复建议
[具体可操作的修复方案]
```

## 终止协议

测试结束时，在最后一条消息的独立行输出终止标记（仅此内容，不要其他文字）：

```
STATUS: VULN_FOUND
STATUS: LOW_ROI
STATUS: NEED_INPUT
```

- **VULN_FOUND**: 发现了至少一个通过七问验证门的漏洞，已用 write_report 保存
- **LOW_ROI**: 经过充分测试未发现符合报告标准的漏洞
- **NEED_INPUT**: 需要人工提供额外信息（凭证/指定测试范围/确认下一步）
```

- [ ] **Step 2: 验证文件存在且非空**

Run: `wc -l "D:/desk/ai测试系统/skills/core-skill.md" && echo "---" && head -5 "D:/desk/ai测试系统/skills/core-skill.md"`
Expected: ~180 行，第一行为 `# AI 渗透测试核心约束`

- [ ] **Step 3: Commit**

```bash
git add skills/core-skill.md
git commit -m "feat: add core skill file v1 (~180 lines, 9 sections)"
```

---

### Task 5: Prompt 构建器

**Files:**
- Create: `D:\desk\ai测试系统\src\engine\prompt_builder.py`
- Create: `D:\desk\ai测试系统\tests\test_prompt_builder.py`

- [ ] **Step 1: 编写 Prompt 构建器**

```python
# src/engine/prompt_builder.py
from pathlib import Path


def load_skill_file(path: Path) -> str:
    """加载核心技能文件内容"""
    if not path.exists():
        raise FileNotFoundError(f"核心技能文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def load_scenario_rules(scenario: str, scenarios_dir: Path) -> str:
    """加载场景专属规则文件，不存在时返回空字符串"""
    rule_file = scenarios_dir / f"{scenario}-rules.md"
    if rule_file.exists():
        return rule_file.read_text(encoding="utf-8")
    return ""


def build_system_prompt(
    core_skill_path: Path,
    target_url: str,
    scenario: str,
    scenarios_dir: Path,
    temp_dir: Path,
    report_dir: Path,
) -> str:
    """组装完整的 system prompt"""
    core = load_skill_file(core_skill_path)
    scenario_rules = load_scenario_rules(scenario, scenarios_dir)

    # 追加会话特定信息
    session_info = f"""
## 当前会话信息

- 目标: {target_url}
- 场景: {scenario}
- 临时目录: {temp_dir}
- 报告目录: {report_dir}
- 工具: curl_http, exec_shell, write_report, finish_session

所有文件操作限制在临时目录内。报告写入报告目录。
"""

    parts = [core]
    if scenario_rules:
        parts.append(scenario_rules)
    parts.append(session_info)
    return "\n\n".join(parts)
```

- [ ] **Step 2: 编写 Prompt 构建器测试**

```python
# tests/test_prompt_builder.py
import tempfile
from pathlib import Path
from src.engine.prompt_builder import (
    load_skill_file,
    load_scenario_rules,
    build_system_prompt,
)


def test_load_skill_file(tmp_path):
    skill = tmp_path / "core-skill.md"
    skill.write_text("# Test skill", encoding="utf-8")
    content = load_skill_file(skill)
    assert content == "# Test skill"


def test_load_skill_file_not_found():
    try:
        load_skill_file(Path("/nonexistent/skill.md"))
        assert False, "应该抛出异常"
    except FileNotFoundError:
        pass


def test_load_scenario_rules(tmp_path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "edu-rules.md").write_text("# edu rules", encoding="utf-8")
    result = load_scenario_rules("edu", scenarios_dir)
    assert result == "# edu rules"


def test_load_scenario_rules_not_found(tmp_path):
    result = load_scenario_rules("nonexistent", tmp_path)
    assert result == ""


def test_build_system_prompt(tmp_path):
    skill = tmp_path / "core-skill.md"
    skill.write_text("CORE SKILL", encoding="utf-8")
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "edu-rules.md").write_text("EDU RULES", encoding="utf-8")
    temp_dir = tmp_path / "sessions" / "test-session"
    temp_dir.mkdir(parents=True)
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    prompt = build_system_prompt(
        core_skill_path=skill,
        target_url="https://example.edu.cn",
        scenario="edu",
        scenarios_dir=scenarios_dir,
        temp_dir=temp_dir,
        report_dir=report_dir,
    )

    assert "CORE SKILL" in prompt
    assert "EDU RULES" in prompt
    assert "https://example.edu.cn" in prompt
    assert str(temp_dir) in prompt
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_prompt_builder.py -v`
Expected: 5 PASS

- [ ] **Step 4: Commit**

```bash
git add src/engine/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: add prompt builder with skill file loading and scenario rules"
```

---

### Task 6: DeepSeek API 客户端

**Files:**
- Create: `D:\desk\ai测试系统\src\engine\deepseek_client.py`

- [ ] **Step 1: 编写 DeepSeek 客户端**

```python
# src/engine/deepseek_client.py
import json
import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

from src.config import Settings

logger = logging.getLogger(__name__)

# Tool definitions sent to DeepSeek
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "curl_http",
            "description": "发送 HTTP 请求（支持 GET/POST/PUT/DELETE），返回响应头和响应体",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 URL"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                        "default": "GET",
                    },
                    "headers": {
                        "type": "object",
                        "description": "HTTP 请求头，如 {\"Cookie\": \"session=xxx\"}",
                    },
                    "body": {
                        "type": "string",
                        "description": "请求体（POST/PUT 时使用）",
                    },
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "在沙箱临时目录内执行 shell 命令（仅允许 curl/wget/python/nslookup/dig 等网络测试工具）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": "发现漏洞时，将漏洞报告写入文件。调用前必须通过七问验证门",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "报告文件名，如 session-id__sqli-login.md"},
                    "content": {"type": "string", "description": "Markdown 格式的完整漏洞报告"},
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_session",
            "description": "结束当前测试会话",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["VULN_FOUND", "LOW_ROI", "NEED_INPUT"],
                        "description": "会话终止状态",
                    },
                    "summary": {
                        "type": "string",
                        "description": "测试总结（发现的漏洞/测试过但未发现的方向）",
                    },
                },
                "required": ["status", "summary"],
            },
        },
    },
]


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self.model = settings.deepseek_model

    async def chat_stream(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
    ) -> AsyncIterator[dict]:
        """流式调用 DeepSeek，逐个产出 delta chunk 或 tool_call"""
        full_messages = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=max_tokens,
            stream=True,
        )

        # 聚合 tool_calls（流式模式下 tool_call 分片到达）
        tool_call_buffer: dict[int, dict] = {}
        finish_reason = None

        async for chunk in stream:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                yield {"type": "text", "content": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_buffer:
                        tool_call_buffer[idx] = {
                            "id": tc.id or "",
                            "function": {"name": "", "arguments": ""},
                        }
                    if tc.id:
                        tool_call_buffer[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_call_buffer[idx]["function"]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_call_buffer[idx]["function"]["arguments"] += tc.function.arguments

        # 流结束后产出完整的 tool_calls
        if finish_reason == "tool_calls" and tool_call_buffer:
            for tc in tool_call_buffer.values():
                try:
                    tc["function"]["arguments_parsed"] = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    tc["function"]["arguments_parsed"] = {}
                yield {"type": "tool_call", "tool_call": tc}

        if finish_reason == "stop":
            yield {"type": "finish", "reason": "stop"}

    async def health_check(self) -> bool:
        """快速探测 API 是否可用"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                stream=False,
            )
            return response.choices[0].message.content is not None
        except Exception:
            return False
```

- [ ] **Step 2: 冒烟测试（需要 API Key）**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -c "
import asyncio
from src.config import load_settings
from src.engine.deepseek_client import DeepSeekClient

async def main():
    settings = load_settings()
    if not settings.deepseek_api_key:
        print('SKIP: No API key configured')
        return
    client = DeepSeekClient(settings)
    healthy = await client.health_check()
    print(f'API Health: {healthy}')

asyncio.run(main())
"`

- [ ] **Step 3: Commit**

```bash
git add src/engine/deepseek_client.py
git commit -m "feat: add DeepSeek API client with streaming and tool definitions"
```

---

### Task 7: Tool 执行器

**Files:**
- Create: `D:\desk\ai测试系统\src\engine\tool_executor.py`
- Create: `D:\desk\ai测试系统\tests\test_tool_executor.py`（先写框架，本任务不实现复杂测试）

- [ ] **Step 1: 编写 Tool 执行器**

```python
# src/engine/tool_executor.py
import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 允许在 exec_shell 中执行的命令白名单
ALLOWED_COMMANDS = {"curl", "wget", "nslookup", "dig", "host", "whois", "python", "python3"}

# 禁止在命令中出现的危险模式
BLOCKED_PATTERNS = [
    "rm -rf", "rm -r", "rmdir",
    ">/dev/", ">/etc/", ">/proc/", ">/sys/",
    "mkfs", "dd if=", "shutdown", "reboot",
    "nc -l", "nc -e", "bash -i", "/bin/bash",
    "> /dev/sda", "chmod 777", "chown",
    "wget -O /", "curl -o /",
]


def _is_command_safe(command: str, allowed_dir: Path) -> tuple[bool, str]:
    """检查命令是否安全可执行"""
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return False, f"命令包含危险模式: {pattern}"

    # 必须有白名单命令开头
    cmd_parts = shlex.split(command)
    if not cmd_parts:
        return False, "空命令"

    cmd_name = cmd_parts[0]
    if cmd_name not in ALLOWED_COMMANDS:
        return False, f"命令不在白名单: {cmd_name}"

    # 检查是否有写入到非临时目录的操作
    if " -o " in command or " -O " in command or " --output-document" in command:
        return False, "不允许通过 exec_shell 写入文件，使用 write_report"

    return True, ""


async def execute_curl(tool_args: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    """执行 curl_http tool call"""
    url = tool_args["url"]
    method = tool_args.get("method", "GET").upper()
    headers = tool_args.get("headers", {})
    body = tool_args.get("body")

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
            )
            return {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response.text[:8000],  # 截断
                "body_length": len(response.text),
            }
    except httpx.TimeoutException:
        return {"error": f"请求超时 ({timeout}s)", "status_code": 0}
    except Exception as e:
        return {"error": str(e), "status_code": 0}


async def execute_shell(tool_args: dict[str, Any], allowed_dir: Path) -> dict[str, Any]:
    """执行 exec_shell tool call（受限）"""
    command = tool_args["command"]
    safe, reason = _is_command_safe(command, allowed_dir)
    if not safe:
        return {"error": reason, "stdout": "", "stderr": reason}

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(allowed_dir),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:4000],
            "stderr": stderr.decode("utf-8", errors="replace")[:2000],
        }
    except asyncio.TimeoutError:
        return {"error": "命令执行超时 (30s)", "stdout": "", "stderr": "timeout"}


async def execute_write_report(tool_args: dict[str, Any], report_dir: Path) -> dict[str, Any]:
    """写入漏洞报告到文件"""
    filename = tool_args["filename"]
    content = tool_args["content"]

    # 安全校验：文件名不能包含路径穿越
    if ".." in filename or "/" in filename or "\\" in filename:
        return {"error": "文件名不能包含路径分隔符"}

    filepath = report_dir / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")
    return {"file_path": str(filepath), "size": len(content)}


async def execute_tool_call(
    tool_call: dict,
    temp_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    """路由 tool call 到对应执行器"""
    name = tool_call["function"]["name"]
    args = tool_call["function"].get("arguments_parsed", {})

    if name == "curl_http":
        result = await execute_curl(args)
    elif name == "exec_shell":
        result = await execute_shell(args, temp_dir)
    elif name == "write_report":
        result = await execute_write_report(args, report_dir)
    elif name == "finish_session":
        result = {"acknowledged": True, "status": args.get("status", "UNKNOWN")}
    else:
        result = {"error": f"Unknown tool: {name}"}

    return {
        "tool_call_id": tool_call["id"],
        "role": "tool",
        "content": str(result),
    }
```

- [ ] **Step 2: 编写基础测试**

```python
# tests/test_tool_executor.py
import tempfile
from pathlib import Path
from src.engine.tool_executor import (
    _is_command_safe,
    execute_write_report,
    execute_curl,
)


def test_is_command_safe_allows_curl():
    safe, reason = _is_command_safe("curl https://example.com", Path("/tmp/test"))
    assert safe


def test_is_command_safe_blocks_dangerous():
    safe, reason = _is_command_safe("rm -rf /", Path("/tmp/test"))
    assert not safe
    assert "危险" in reason


def test_is_command_safe_requires_whitelist():
    safe, reason = _is_command_safe("cat /etc/passwd", Path("/tmp/test"))
    assert not safe
    assert "白名单" in reason


def test_write_report(tmp_path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    result = execute_write_report(
        {"filename": "test.md", "content": "# Test Report"},
        report_dir,
    )
    assert result["size"] > 0
    assert (report_dir / "test.md").exists()


def test_write_report_blocks_path_traversal():
    result = execute_write_report(
        {"filename": "../../etc/passwd", "content": "bad"},
        Path("/tmp/test"),
    )
    assert "error" in result
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_tool_executor.py -v`
Expected: 5 PASS

- [ ] **Step 4: Commit**

```bash
git add src/engine/tool_executor.py tests/test_tool_executor.py
git commit -m "feat: add tool executor with curl/shell/report/finish support"
```

---

### Task 8: 会话主循环

**Files:**
- Create: `D:\desk\ai测试系统\src\engine\session.py`
- Create: `D:\desk\ai测试系统\tests\test_session.py`

- [ ] **Step 1: 编写会话主循环**

```python
# src/engine/session.py
import asyncio
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from src.config import Settings
from src.models import Session, SessionStatus
from src.database import init_db, insert_session, update_session_status
from src.engine.prompt_builder import build_system_prompt
from src.engine.deepseek_client import DeepSeekClient
from src.engine.tool_executor import execute_tool_call

logger = logging.getLogger(__name__)

STATUS_MARKER_PREFIX = "STATUS:"


async def create_session_dir(session_id: str, session_root: Path) -> Path:
    """创建会话临时目录"""
    d = session_root / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def detect_status_marker(text: str) -> str | None:
    """从文本中检测状态标记"""
    for line in reversed(text.strip().split("\n")):
        line = line.strip()
        if line.startswith(STATUS_MARKER_PREFIX):
            status = line[len(STATUS_MARKER_PREFIX):].strip()
            if status in ("VULN_FOUND", "LOW_ROI", "NEED_INPUT"):
                return status
    return None


async def run_session(
    settings: Settings,
    target_url: str,
    scenario: str = "custom",
    project_id: str = "default",
    priority: int = 5,
) -> str:
    """
    运行一个完整的 AI 渗透测试会话。
    返回终态: vuln_found / low_roi / need_input / error
    """
    session_id = str(uuid.uuid4())[:8]
    temp_dir = await create_session_dir(session_id, settings.session_dir)
    report_dir = settings.report_dir
    report_dir.mkdir(parents=True, exist_ok=True)

    # 初始化数据库
    db = await init_db(settings.database_path)

    session = Session(
        id=session_id,
        project_id=project_id,
        scenario=scenario,
        target_url=target_url,
        status=SessionStatus.RUNNING,
        priority=priority,
        temp_dir=temp_dir,
        report_dir=report_dir,
        started_at=datetime.now().isoformat(),
    )
    await insert_session(db, session)

    # 构建 system prompt
    system_prompt = build_system_prompt(
        core_skill_path=settings.skill_file,
        target_url=target_url,
        scenario=scenario,
        scenarios_dir=Path("scenarios"),
        temp_dir=temp_dir,
        report_dir=report_dir,
    )

    # 初始化 DeepSeek 客户端
    client = DeepSeekClient(settings)
    messages: list[dict] = []
    status_marker = None
    turn_count = 0
    start_time = time.time()
    timeout_seconds = settings.session_timeout_hours * 3600

    try:
        while turn_count < settings.session_max_turns:
            # 检查时间上限
            if time.time() - start_time > timeout_seconds:
                logger.warning(f"会话 {session_id} 超时")
                await update_session_status(db, session_id, "error", "硬时间上限")
                return "error"

            # 检查磁盘配额
            if _check_disk_quota(temp_dir, settings.session_disk_limit_gb):
                logger.warning(f"会话 {session_id} 磁盘配额超限")
                await update_session_status(db, session_id, "error", "磁盘配额超限")
                return "error"

            turn_count += 1
            logger.info(f"[{session_id}] Turn {turn_count}/{settings.session_max_turns}")

            # 调用 DeepSeek
            try:
                async for event in client.chat_stream(system_prompt, messages):
                    if event["type"] == "text":
                        print(event["content"], end="", flush=True)

                    elif event["type"] == "tool_call":
                        tc = event["tool_call"]
                        logger.info(f"[{session_id}] Tool call: {tc['function']['name']}")

                        # 执行 tool
                        result = await execute_tool_call(tc, temp_dir, report_dir)

                        # 追加到消息历史
                        messages.append({
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["function"]["name"],
                                    "arguments": tc["function"]["arguments"],
                                },
                            }],
                        })
                        messages.append(result)

                        # finish_session 由 AI 显式调用
                        if tc["function"]["name"] == "finish_session":
                            status_marker = tc["function"].get("arguments_parsed", {}).get("status")
                            logger.info(f"[{session_id}] AI 主动结束: {status_marker}")
                            break

                    elif event["type"] == "finish":
                        # AI 自然结束但没有 tool call
                        pass

            except Exception as e:
                logger.error(f"[{session_id}] API 调用失败: {e}")
                await update_session_status(db, session_id, "error", str(e))
                return "error"

            # 如果 finish_session 被调用过，退出循环
            if status_marker:
                break

            print()  # 每轮换行

    finally:
        await db.close()

    # 终态判定
    final_status = _determine_status(status_marker, report_dir, session_id)
    final_status_str = final_status.value if hasattr(final_status, 'value') else final_status
    await _finalize_session(settings.database_path, session_id, final_status_str)
    return final_status_str


def _check_disk_quota(temp_dir: Path, limit_gb: int) -> bool:
    """检查临时目录是否超过配额"""
    if not temp_dir.exists():
        return False
    total = sum(f.stat().st_size for f in temp_dir.rglob("*") if f.is_file())
    return total > limit_gb * 1024 * 1024 * 1024


def _determine_status(marker: str | None, report_dir: Path, session_id: str) -> str:
    """12条决策规则的核心逻辑（MVP 简化版）"""
    # 检查是否有有效报告
    has_report = False
    for f in report_dir.glob(f"{session_id}*.md"):
        if f.stat().st_size >= 200:
            has_report = True
            break

    # 规则引擎
    if marker == "VULN_FOUND" and has_report:
        return "vuln_found"          # 规则4
    if marker == "VULN_FOUND" and not has_report:
        return "low_roi"             # 规则5: 证据推翻声明
    if marker == "LOW_ROI" and has_report:
        return "vuln_found"          # 规则6: 证据推翻声明
    if marker == "LOW_ROI" and not has_report:
        return "low_roi"             # 规则7
    if marker == "NEED_INPUT":
        return "need_input"          # 规则8
    if marker is None and has_report:
        return "vuln_found"          # 规则9: 补救
    if marker is None:
        return "error"               # 规则10/11
    return "error"                   # 规则12: 兜底


async def _finalize_session(db_path: Path, session_id: str, status: str) -> None:
    """最终写入会话状态"""
    db = await aiosqlite.connect(str(db_path))
    await update_session_status(db, session_id, status)
    await db.close()
```

- [ ] **Step 2: 编写会话核心逻辑测试**

```python
# tests/test_session.py
from pathlib import Path
from src.engine.session import detect_status_marker, _determine_status, _check_disk_quota


def test_detect_vuln_found():
    text = "测试完成\nSTATUS: VULN_FOUND"
    assert detect_status_marker(text) == "VULN_FOUND"


def test_detect_low_roi():
    text = "无发现\nSTATUS: LOW_ROI"
    assert detect_status_marker(text) == "LOW_ROI"


def test_detect_need_input():
    text = "需要信息\nSTATUS: NEED_INPUT"
    assert detect_status_marker(text) == "NEED_INPUT"


def test_detect_no_marker():
    text = "测试完成，没有标记"
    assert detect_status_marker(text) is None


def test_detect_marker_not_last_line():
    text = "STATUS: VULN_FOUND\n其他文字"
    assert detect_status_marker(text) == "VULN_FOUND"


def test_determine_status_vuln_found_with_report(tmp_path):
    # 创建模拟报告
    (tmp_path / "abc123__sqli.md").write_text("x" * 200)
    result = _determine_status("VULN_FOUND", tmp_path, "abc123")
    assert result == "vuln_found"


def test_determine_status_vuln_found_no_report(tmp_path):
    result = _determine_status("VULN_FOUND", tmp_path, "abc123")
    assert result == "low_roi"  # 证据推翻


def test_determine_status_low_roi_has_report(tmp_path):
    (tmp_path / "abc123__idor.md").write_text("x" * 200)
    result = _determine_status("LOW_ROI", tmp_path, "abc123")
    assert result == "vuln_found"  # 证据推翻


def test_determine_status_no_marker_has_report(tmp_path):
    (tmp_path / "abc123__rce.md").write_text("x" * 200)
    result = _determine_status(None, tmp_path, "abc123")
    assert result == "vuln_found"  # 补救


def test_check_disk_quota_under_limit(tmp_path):
    (tmp_path / "small.txt").write_text("small")
    assert not _check_disk_quota(tmp_path, 5)  # 5GB limit, 几byte远未到


def test_determine_status_no_marker_no_report(tmp_path):
    result = _determine_status(None, tmp_path, "abc123")
    assert result == "error"
```

- [ ] **Step 3: 运行测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/test_session.py -v`
Expected: 11 PASS

- [ ] **Step 4: Commit**

```bash
git add src/engine/session.py tests/test_session.py
git commit -m "feat: add session runner with turn loop, status detection, and termination logic"
```

---

### Task 9: 教育系统场景规则

**Files:**
- Create: `D:\desk\ai测试系统\scenarios\edu-rules.md`

- [ ] **Step 1: 创建教育系统场景规则**

```markdown
# 教育系统额外约束

> 本文件与核心技能文件合并加载，覆盖/追加以下规则。

## 漏洞收录门槛（覆盖默认标准）

| 漏洞类型 | 最低要求 |
|---------|---------|
| 信息泄露 | 身份证号 / 数据库密码 / AK/SK / 源码 / 敏感配置。仅有学号/姓名/班级不算 |
| SQL 注入 | **必须出库名** (database name)。仅报错/延迟不够 |
| XSS | **仅存储型**。反射型不收 |
| 任意文件下载 | 读取任意文件（/etc/passwd、源码等） |
| 文件覆盖/删除 | **不能操作系统文件**，只操作自己的或可复原的文件 |
| 越权/IDOR | 需确认返回的是**真实他人数据**而非默认值/空值 |
| SSRF | **仅限云 Metadata 拿凭据**（169.254.169.254、100.100.100.200），禁止探测内网/端口 |

## 测试红线

- **禁止**: 操作系统文件、探测内网端口、删除他人数据
- **只读优先**: 先从只读请求开始，确认漏洞后不扩大影响
- **复原检查**: 测试退出前逐项检查所有可修改项，确保恢复原状

## 测试后复原

测试结束后必须：
1. 记录所有修改过的数据
2. 逐项恢复原状
3. 在最终报告中注明"已复原"或"已通知管理员恢复"
```

- [ ] **Step 2: Commit**

```bash
git add scenarios/edu-rules.md
git commit -m "feat: add education system scenario rules"
```

---

### Task 10: CLI 入口

**Files:**
- Create: `D:\desk\ai测试系统\cli\avs.py`

- [ ] **Step 1: 编写 CLI 入口**

```python
#!/usr/bin/env python3
"""AI Vulnerability Scanner — CLI entry point (MVP)"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_settings
from src.engine.session import run_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("avs")


def main():
    parser = argparse.ArgumentParser(
        description="AI 辅助漏洞扫描器 (MVP)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  avs https://target.edu.cn
  avs https://target.edu.cn -s edu -p 10
  avs https://target.edu.cn -s custom --project "my-project"
        """,
    )
    parser.add_argument("target", help="目标 URL")
    parser.add_argument("-s", "--scenario", default="custom",
                        choices=["custom", "edu", "src"],
                        help="测试场景 (default: custom)")
    parser.add_argument("-p", "--priority", type=int, default=5,
                        help="优先级 1-10 (default: 5)")
    parser.add_argument("--project", default="default",
                        help="项目标识 (default: default)")
    parser.add_argument("--env", default=".env",
                        help="环境变量文件路径 (default: .env)")

    args = parser.parse_args()

    # 加载配置
    settings = load_settings()

    if not settings.deepseek_api_key:
        logger.error("未配置 DEEPSEEK_API_KEY。请在 .env 文件中设置。")
        logger.error("  cp .env.example .env  &&  编辑 .env 填入 API Key")
        sys.exit(1)

    logger.info(f"目标: {args.target}")
    logger.info(f"场景: {args.scenario}")
    logger.info(f"模型: {settings.deepseek_model}")
    logger.info(f"报告目录: {settings.report_dir.resolve()}")
    logger.info(f"临时目录: {settings.session_dir.resolve()}")
    logger.info("=" * 60)

    # 运行会话
    final_status = asyncio.run(run_session(
        settings=settings,
        target_url=args.target,
        scenario=args.scenario,
        project_id=args.project,
        priority=args.priority,
    ))

    logger.info("=" * 60)
    logger.info(f"终态: {final_status}")

    if final_status == "vuln_found":
        logger.info("发现漏洞！查看 data/reports/ 目录")
    elif final_status == "low_roi":
        logger.info("本轮未发现符合报告标准的漏洞")
    elif final_status == "need_input":
        logger.info("AI 需要更多信息才能继续")
    else:
        logger.error("会话异常终止")

    sys.exit(0 if final_status in ("vuln_found", "low_roi") else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试 CLI help 输出**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" cli/avs.py --help`
Expected: 显示帮助信息，列出所有参数

- [ ] **Step 3: Commit**

```bash
git add cli/avs.py
git commit -m "feat: add CLI entry point with argparse interface"
```

---

### Task 11: 完善测试与集成

**Files:**
- Create: `D:\desk\ai测试系统\tests\__init__.py` (empty)

- [ ] **Step 1: 创建 tests/__init__.py**

Write empty file at `tests/__init__.py`

- [ ] **Step 2: 运行全部测试**

Run: `cd "D:/desk/ai测试系统" && "D:/desk/tools/python/py3.14.5/python.exe" -m pytest tests/ -v --tb=short`
Expected: All tests pass (约 20+ tests)

- [ ] **Step 3: 创建 .gitignore**

```gitignore
# .gitignore
.env
data/
__pycache__/
*.pyc
.egg-info/
dist/
.pytest_cache/
.superpowers/
```

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py .gitignore
git commit -m "chore: finalize MVP test suite and add .gitignore"
```

---

### Task 12: 首次实战验证

> 此任务不需要提交代码，是验证步骤。

- [ ] **Step 1: 配置 API Key**

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

- [ ] **Step 2: 对测试目标运行**

```bash
cd "D:/desk/ai测试系统"
"D:/desk/tools/python/py3.14.5/python.exe" cli/avs.py https://httpbin.org -s custom
```

观察：
- AI 是否开始自主测试
- Tool call 是否正确执行
- 输出流是否正常
- 是否能正常终止

- [ ] **Step 3: 分析 AI 行为**

- 它做了什么操作？
- 它是否遵守了核心技能文件的约束？
- 它报了什么？是否有噪音？

- [ ] **Step 4: 根据表现迭代核心技能文件**

按迭代表更新 `skills/core-skill.md`：
- 报了噪音 → 加到垃圾洞清单
- 做了危险操作 → 加到铁律
- 报告质量差 → 强化七问门
- 死磕某方向 → 调整时间约束

---

## 验证清单

在声称 MVP 完成前确认：

- [ ] `pytest tests/ -v` 全部通过
- [ ] `cli/avs.py --help` 正常输出
- [ ] `pyproject.toml` 依赖声明完整
- [ ] `.env.example` 包含所有必要变量
- [ ] `skills/core-skill.md` 约 180 行，9 个区域齐全
- [ ] `scenarios/edu-rules.md` 包含教育系统收录标准
- [ ] CLI 对真实目标至少完成一次完整会话
- [ ] AI 行为符合核心技能文件约束
