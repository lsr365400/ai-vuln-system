# AI 辅助漏洞挖掘系统 — 详细设计文档

版本 2.0 | 2026-06-16

---

## 目录

1. [系统定位与目标](#1-系统定位与目标)
2. [核心设计哲学](#2-核心设计哲学)
3. [技术决策汇总](#3-技术决策汇总)
4. [系统总体架构](#4-系统总体架构)
5. [核心技能文件设计](#5-核心技能文件设计)
6. [会话调度与并发控制](#6-会话调度与并发控制)
7. [会话生命周期管理](#7-会话生命周期管理)
8. [终态判定与证据系统](#8-终态判定与证据系统)
9. [实时通信与前端设计](#9-实时通信与前端设计)
10. [报告自动化](#10-报告自动化)
11. [安全防护机制](#11-安全防护机制)
12. [数据模型](#12-数据模型)
13. [场景配置系统](#13-场景配置系统)
14. [实施路线图](#14-实施路线图)
15. [项目目录结构](#15-项目目录结构)
16. [迭代哲学](#16-迭代哲学)

---

## 1. 系统定位与目标

### 1.1 定位

通用漏洞挖掘引擎 + 场景配置层切换规则。核心引擎不做任何行业假设，通过"场景配置"动态加载不同的规则集和收录标准。

### 1.2 目标

- 自动化渗透测试：AI 自主推理、选择攻击路径、执行验证
- 人类只定义边界：不教方法，只设行为约束和报告标准
- 多场景覆盖：教育系统（edu-rules）、通用 SRC、自定义场景

### 1.3 部署模式

先单机 CLI MVP，通信层从第一天起按 REST + WebSocket API 设计。CLI 是第一个消费端，后续加 Web 前端不改后端。

---

## 2. 核心设计哲学

### 2.1 不教方法，只设边界

> AI 已经会做渗透测试。我们只需要告诉它什么不该做、什么不该报。

- 信任 AI 的测试方法选择、推理能力、知识储备
- 不信任 AI 的自制力（限制危险操作）
- 不信任 AI 的报告标准（明确什么值得报）

### 2.2 灵魂金句

> 现象不是漏洞，漏洞是结果。报的是结果（越权/注入/RCE），不是过程（信息泄露/配置问题）。

### 2.3 一个核心技能文件

整个系统只需要一个核心技能文件（~180 行）。不给每种漏洞写单独的方法论——AI 已经知道怎么测。文件只定义边界和报告标准。

---

## 3. 技术决策汇总

| 维度 | 决策 | 理由 |
|------|------|------|
| 场景定位 | 通用引擎 + 场景配置层 | 一次建设，多场景复用 |
| 部署模式 | CLI MVP → Web 平台，API 原生 | 快速验证，架构不返工 |
| 模型接入 | DeepSeek API 单一绑定 | 减少复杂度，深度优化 prompt |
| 执行环境 | 宿主机沙箱目录 + 磁盘防护 | 简单高效，防护层兜底 |
| 技术栈 | Python FastAPI 全栈 | 异步原生、生态成熟、与安全工具一致 |
| 架构方案 | 一体化异步引擎（方案 A） | MVP 足够，后续可升级 |
| 数据库 | SQLite (WAL 模式) | 零运维 |
| 前端 | 后续 Web 阶段用 Vue 3 + Tailwind CSS | 生态成熟 |

---

## 4. 系统总体架构

### 4.1 架构方案：一体化异步引擎

所有组件在一个 FastAPI 进程内，AI Worker 为 asyncio 协程。

```
┌─────────────────────────────────────────────────────────┐
│                   FastAPI Application                    │
│                                                         │
│  ┌──────────────────┐    ┌──────────────────────────┐  │
│  │   REST API Layer  │    │   WebSocket Layer        │  │
│  │  POST /sessions   │    │  ws /sessions/{id}/flow  │  │
│  │  GET  /reports    │    │  ws /dashboard/overview  │  │
│  │  PUT  /settings   │    │  ws /control             │  │
│  └────────┬─────────┘    └───────────┬──────────────┘  │
│           │                          │                  │
│  ┌────────▼──────────────────────────▼──────────────┐  │
│  │         Event Bus (asyncio.Queue per-channel)     │  │
│  └──┬───────┬───────┬──────────┬───────────────────┘  │
│     │       │       │          │                       │
│  ┌──▼──┐ ┌──▼──┐ ┌──▼──────┐ ┌▼──────────────────┐   │
│  │Task 1│ │Task 2│ │Scheduler│ │Session Registry   │   │
│  │ai_wk │ │ai_wk │ │PriorityQ│ │active / queued    │   │
│  └──┬──┘ └──┬──┘ └─────────┘ └───────────────────┘   │
│     │       │                                          │
│  ┌──▼───────▼──────────────────────────────────────┐   │
│  │          Safety Guard (per-session)              │   │
│  │  Disk quota · Path guard · API health · Orphan   │   │
│  └────────────────────┬────────────────────────────┘   │
│                       │                                │
│  ┌────────────────────▼────────────────────────────┐   │
│  │  SQLite (sessions/reports/settings) + FileSystem │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 4.2 外部依赖（仅 3 项）

| 依赖 | 用途 |
|------|------|
| DeepSeek API | AI 推理与工具调用，通过 HTTP SDK 调用 |
| SQLite | 会话状态、报告索引、配置存储 |
| 文件系统 | 报告 .md 文件、临时沙箱目录 |

### 4.3 数据流

```
Client (CLI / Web)
  → POST /sessions {target, scenario, apikey, ...}
  → API → Scheduler: 入队 → 等待槽位 → 分配槽位
  → AI Worker Task (asyncio):
      1. 加载核心技能文件
      2. 构建 System Prompt
      3. 循环: call DeepSeek → 解析 tool_use → exec → send result → event_bus
      4. 检测状态标记 / 超时
  → Termination Detector: 12条规则判定
  → Report Indexer: 解析 .md → 提取 severity/title → 入库 → 通知
  → Client (via WebSocket / REST)
```

---

## 5. 核心技能文件设计

### 5.1 六大准则

1. **垃圾洞清单置顶** — AI 第一眼看到的就是不该报什么
2. **灵魂金句** — 现象不是漏洞，漏洞是结果
3. **只装决策逻辑，不装 payload** — 你不需要教鲨鱼游泳
4. **防遗忘机制** — 速查卡 + Phase 切换检查点
5. **七问验证门** — 报告前的终极过滤
6. **决策树而非固定流程** — 按目标特征动态分支

### 5.2 文件结构总览（~180 行）

| # | 区域 | 行数 | 内容 |
|---|------|------|------|
| 1 | 垃圾洞清单 | 25 | CORS/Sourcemap/安全头/版本号/Self-XSS/SSL/开放重定向/RateLimit/无PoC |
| 2 | 灵魂金句 | 3 | 现象不是漏洞，漏洞是结果 |
| 3 | 速查卡 | 18 | 15条核心规则短句 |
| 4 | 铁律 | 12 | 禁止 rm -rf / 反弹Shell / 内网扫描 / 修改他人数据 |
| 5 | 七问验证门 | 20 | 报告前必须自答的 7 个问题 |
| 6 | 决策树 | 45 | 按登录态/技术栈/功能/ROI 动态分支，20min 无进展换方向 |
| 7 | 报告格式规范 | 12 | frontmatter + 5段式正文 + curl PoC + 修复建议 |
| 8 | 终止协议 | 8 | VULN_FOUND/LOW_ROI/NEED_INPUT 状态标记规范 |
| 9 | 防遗忘指令 | 8 | Phase 切换时重读速查卡，30min 强制重读 |

### 5.3 垃圾洞清单（置顶，绝对不报）

| 绝对不报 | 原因 |
|---------|------|
| CORS 跨域配置 | 跨域本身不是漏洞，除非证明窃取了具体数据 |
| Sourcemap 泄露 | 配置问题，不是可利用的安全漏洞 |
| HTTP 安全头缺失 | 理论风险 |
| 版本号/中间件指纹 | 信息收集副产品 |
| Self-XSS | 只能攻击自己 |
| SSL/TLS 配置警告 | 除非严重降级攻击 |
| 单独的开放重定向 | 无法链式利用几乎无害 |
| Rate limiting 缺失 | 功能建议 |
| 任何没有 PoC 的发现 | 不能重现就不存在 |

### 5.4 七问验证门

AI 写报告前必须逐条自答，全部通过才能动笔：

| # | 问题 | 不通过则 |
|---|------|---------|
| 1 | 在授权范围内吗？ | 停止，不报 |
| 2 | 有完整 PoC 吗（curl/HTTP）？ | 补充，没有不报 |
| 3 | 需要假设或推测来解释危害吗？ | 需要假设 → 不报 |
| 4 | 影响是已证明的还是"可能"的？ | "可能" → 不报 |
| 5 | 现象还是结果？ | 现象 → 不报 |
| 6 | 不懂安全的开发者能理解危害吗？ | 写清楚，或不够明显不报 |
| 7 | 发到漏洞平台会被接受还是关闭？ | 会被关闭 → 不报 |

### 5.5 决策树

```
START
  ├─ [有登录态] → 越权/IDOR → 水平→垂直→敏感数据
  ├─ [无登录态] → 未授权访问→信息泄露→注入点
  ├─ [技术栈]
  │   ├─ Java/Spring → JNDI→反序列化→Actuator→SpEL
  │   ├─ PHP → LFI→SQLi→文件上传→反序列化
  │   ├─ Node.js → NoSQL注入→原型污染→SSJI
  │   ├─ Python → SSTI→反序列化(pickle)→SQLi
  │   └─ Go → 命令注入→路径穿越→CRLF
  ├─ [功能分支-ROI]
  │   ├─ 支付/充值 → 竞态→金额篡改→负数→重复提交
  │   ├─ 文件上传 → 类型绕过→路径穿越→图片马
  │   ├─ 数据导出 → 注入→越权→格式注入
  │   ├─ API → BOLA→参数污染→mass assignment
  │   └─ 搜索/过滤 → SQLi→XSS→命令注入
  └─ [时间约束]
      ├─ 同一攻击面 >20min 无进展 → 强制换方向
      └─ 切换前 → 重读速查卡
```

### 5.6 速查卡（15 条）

```
CORS ≠ 漏洞            | 无 PoC ≠ 漏洞
现象 ≠ 结果            | Self-XSS = 垃圾
安全头缺失 = 0元       | Sourcemap = 0元
20min无进展 → 换方向   | 30min → 重读速查卡
报告必须有 curl        | PoC 必须可重现
P3 以下不写报告        | 禁止 rm -rf
禁止反弹 Shell         | 禁止内网扫描
报的是结果，不是过程
```

---

## 6. 会话调度与并发控制

### 6.1 状态机（6 态）

```
POST /sessions → queued
queued → running (scheduler 分配槽位)
running → vuln_found / low_roi / need_input / error / stopped
```

终态定义：

| 终态 | 含义 | 后续 |
|------|------|------|
| vuln_found | 发现有 PoC 的真实漏洞 | 查看报告，提交 |
| low_roi | 无有价值发现 | 换方法或跳过 |
| need_input | 需要人工输入 | 提供后恢复 |
| error | 系统错误 | 排查重试 |
| stopped | 用户停止 | 按需重启 |

### 6.2 并发控制参数

| 参数 | 建议值 | 机制 |
|------|--------|------|
| 全局并发上限 | 5-10 | asyncio.Semaphore |
| 每项目上限 | 全局 / 2 | 按 project_id 计数 |
| 排队策略 | 优先级 + FIFO | 同优先级按创建时间 |
| 公平调度 | 槽位释放时轮询项目 | 防止大项目饿死小项目 |
| 动态扩容 | 运行时 API | PUT /settings 即时生效 |

### 6.3 AI Worker 执行循环

```python
async def run_session(session: Session, safety: SafetyGuard):
    # 1. 构建 System Prompt
    system_prompt = build_prompt(session.scenario, session.target_url,
                                  session.temp_dir, session.report_dir)

    # 2. 主循环
    for turn in range(max_turns=200):
        if safety.check_disk():   # 每轮检查磁盘配额
            break

        response = await deepseek.call(
            messages=history,
            tools=tool_definitions,  # curl_http, exec_shell, write_report, browser_nav
            stream=True,
        )

        async for chunk in response:
            event_bus.publish(session.id, chunk)   # → WebSocket

        for tool_call in response.tool_calls:
            result = await execute_tool(tool_call, session.temp_dir)
            history.append(result)

        if status_marker := detect_marker(response):
            break

    # 3. 终态判定 + 报告索引
    return termination_detector.judge(session, status_marker)
```

### 6.4 超时与重连

- **无活动超时**: 30min 无新输出 → 暂停
- **硬时间上限**: 4h 绝对上限 → 强制终止
- **心跳检测**: API 侧 5min 无响应 → 触发重连
- **自动重连**: 最多 3 次，间隔 10s → 20s → 40s
- **API 不健康**: 连续 5 次失败 → 暂停所有新会话

---

## 7. 会话生命周期管理

### 7.1 关键设计点

- 每个会话一个独立 asyncio 协程，互不干扰
- 接收 DeepSeek 流式输出，分发到 EventBus
- 监控临时目录大小，检测 API 健康
- 超时使用"无活动超时"而非"总时长超时"——AI 可能在深入分析
- 状态标记只从最后一条消息的独立行提取

### 7.2 自动重连

- 最多 3 次，指数退避间隔
- 恢复上下文：历史消息不丢失，从中断处继续
- API 普遍不健康时暂停所有新会话

---

## 8. 终态判定与证据系统

### 8.1 双重验证模型

不单纯相信 AI 的声明。同时检查两个输入：

- **输入 A**: AI 的状态标记声明（最后一条消息的独立行）
- **输入 B**: 磁盘上的物理证据（报告文件是否存在、是否有效）

### 8.2 有效报告标准（4 项硬性指标）

1. 严重等级 P1/P2/P3
2. 有标题（frontmatter title）
3. 正文 >= 200 字符
4. 包含复现证据（curl/HTTP 请求/URL）

### 8.3 十二条决策规则

| # | 条件 | 结果 | 说明 |
|---|------|------|------|
| 1 | 被用户停止 | stopped | 用户意图明确 |
| 2 | 临时目录超限 | error | 安全第一 |
| 3 | 超硬性时间上限 | error | 防止无限循环 |
| 4 | VULN_FOUND + 有效报告 | vuln_found | 声明与证据一致 |
| 5 | VULN_FOUND + 无报告 | low_roi | **证据推翻声明** |
| 6 | LOW_ROI + 有报告 | vuln_found | **证据推翻声明** |
| 7 | LOW_ROI + 无报告 | low_roi | 声明与证据一致 |
| 8 | NEED_INPUT + 无报告 | need_input | 等待输入 |
| 9 | 无标记 + 有报告 | vuln_found | **补救规则** |
| 10 | 无标记 + 正常结束 | error | 协议违规 |
| 11 | 无标记 + 异常结束 | error | 连接异常 |
| 12 | 兜底 | error | 保守策略 |

### 8.4 状态标记协议

AI 必须在最后一条消息的独立行写入：

```
STATUS: VULN_FOUND
STATUS: LOW_ROI
STATUS: NEED_INPUT
```

解析规则：
1. 取 AI 最后一条消息
2. 正则匹配 `/^STATUS:\s*(VULN_FOUND|LOW_ROI|NEED_INPUT)$/m`
3. 不存在匹配 → 判定为"无标记"

---

## 9. 实时通信与前端设计

### 9.1 EventBus

- 发布/订阅模式，内存 asyncio.Queue
- 每个会话独立 channel
- 每个订阅者独立队列（上限 1024）
- 满则丢弃最旧事件

```python
class EventBus:
    channels: dict[str, list[asyncio.Queue]]

    def publish(session_id, event):
        for queue in channels[session_id]:
            if queue.full(): queue.get_nowait()  # 丢弃最旧
            queue.put_nowait(event)

    def subscribe(session_id) -> asyncio.Queue: ...
    def unsubscribe(session_id, queue): ...
```

### 9.2 WebSocket 端点

| 端点 | 方向 | 推送事件 | 用途 |
|------|------|---------|------|
| ws /sessions/{id}/flow | → 客户端 | text_chunk, tool_call, tool_result, status_change, error | 单会话实时 AI 输出 |
| ws /dashboard/overview | → 客户端 | session_started, session_ended, vuln_found, resource_update | 仪表盘全局概览 |
| ws /control | ↔ 双向 | stop_session, restart_session, send_input | 前端控制命令 |

### 9.3 前端页面（5 页面）

| 页面 | 核心组件 | 交互 |
|------|---------|------|
| 仪表盘 | 状态汇总卡片、最近漏洞列表、系统资源仪表、活动会话速览 | 全局 WS 实时推送 |
| 会话列表 | 筛选栏(状态/项目/场景)、排序、批量操作(停止/重启)、分页 | POST 创建新会话 |
| 会话详情 | AI 实时输出瀑布流、tool_call 折叠面板、报告预览、快速指令按钮 | 单会话 WS、发送输入 |
| 报告 | 按等级排序、Markdown 渲染、筛选/搜索、导出、详情页 | 点击查看完整报告 |
| 设置 | 并发上限、模型选择、超时配置、通知邮箱、授权文件上传、场景管理 | PUT 即时生效 |

### 9.4 快速指令

- 「深入调查」— 对某个发现深入测试
- 「生成报告」— 立即生成当前发现的报告
- 「换个方向」— 尝试其他测试角度
- 「停止测试」— 立即终止

---

## 10. 报告自动化

### 10.1 报告格式

```markdown
---
severity: P1
title: 漏洞标题
target: 目标
type: 类型
date: 日期
---
## 漏洞描述
## 影响范围
## 复现步骤（含 curl）
## PoC
## 修复建议
```

### 10.2 报告索引器流程

```python
async def index_report(session_id: str, report_path: Path):
    # 1. 解析 frontmatter
    meta = parse_frontmatter(report_path)
    if meta.severity not in ("P1", "P2", "P3"):
        return  # 跳过 P4+

    # 2. 去重检测
    fingerprint = sha256(meta.title + meta.target)  # 与数据模型一致
    if await db.find_by_fingerprint(fingerprint):
        return

    # 3. 入库
    await db.insert_report(session_id, meta.severity, meta.title,
                            meta.target, meta.type, meta.date, report_path)

    # 4. 通知（P1/P2 邮件）
    if meta.severity in ("P1", "P2"):
        await notify_email(subject=f"[{meta.severity}] {meta.title}", ...)
```

### 10.3 邮件通知

- P1/P2 漏洞：立即发送邮件
- API 健康步数 >= 3：发送告警邮件
- SMTP 可配置

---

## 11. 安全防护机制

### 11.1 四层防护

| 层级 | 机制 | 阈值 | 动作 |
|------|------|------|------|
| 磁盘防护 | **L1**: 自循环检测（写入路径在读取路径子目录 → 拦截）
| | **L2**: 每会话配额 5GB，每 3s 检查 | 5GB/session | 超限 → 立即终止 → ERROR |
| API 健康 | 连续失败计数 → 判定不健康 → 指数退避探测 | 连续 5 次失败 | 暂停新会话，步数≥3 邮件通知 |
| 性能监控 | CPU 70%/90%，内存 75%/90%，磁盘 80%/95% | 警告→减少并发，危险→停止 | 滞后 2min 恢复，防振荡 |
| 孤儿进程 | 每 5min 扫描 | 5min 周期 | kill → 日志，启动时 running→queued |

### 11.2 API 健康退避

- 探测间隔：10s → 20s → 40s → 80s → 160s → 240s（上限）
- 探测成功自动恢复
- 步数 >= 3 邮件通知

### 11.3 性能降级策略

- 渐进降级：警告 → 减少并发 → 危险 → 停止新会话
- 滞后恢复：危险解除后等 2min 再恢复，防振荡
- 启动恢复：running → queued，重扫报告索引，清理临时文件

---

## 12. 数据模型

### 12.1 SQLite 5 表（WAL 模式）

```sql
TABLE sessions (
    id              TEXT PRIMARY KEY,        -- UUID
    project_id      TEXT NOT NULL,
    scenario        TEXT NOT NULL,           -- edu / src / custom
    target_url      TEXT NOT NULL,
    status          TEXT DEFAULT 'queued',
    priority        INTEGER DEFAULT 5,
    temp_dir        TEXT,
    report_dir      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    error_msg       TEXT
);

TABLE reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    severity        TEXT NOT NULL,           -- P1 / P2 / P3
    title           TEXT NOT NULL,
    target          TEXT NOT NULL,
    type            TEXT NOT NULL,
    fingerprint     TEXT UNIQUE,             -- sha256(title+target)
    file_path       TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

TABLE settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,           -- JSON value
    updated_at      TEXT DEFAULT (datetime('now'))
);

TABLE scenarios (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    skill_file_path TEXT NOT NULL,
    rules_path      TEXT,
    is_active       INTEGER DEFAULT 1
);

TABLE event_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,           -- JSON
    created_at      TEXT DEFAULT (datetime('now'))
);
```

---

## 13. 场景配置系统

### 13.1 设计

核心引擎完全通用，不做行业假设。创建会话时指定场景标识，引擎构建 prompt 时动态加载场景专属规则文件。

### 13.2 实现

```python
core_skill    = load("skills/core-skill.md")        # 通用核心
scenario_rule = load(f"scenarios/{scenario}-rules.md")  # 场景专属
system_prompt = core_skill + "\n\n" + scenario_rule
```

### 13.3 场景规则示例（edu-rules.md）

- 信息泄露必须含：身份证号/数据库密码/AK-SK/源码，仅有学号/姓名/班级不算
- SQL 注入必须出库名，仅报错/延迟不够
- XSS 仅收存储型，反射型不收
- SSRF 仅限云 Metadata 拿凭据，禁止探测内网
- 测试结束必须复原账号信息

---

## 14. 实施路线图

### 阶段一：MVP（1-2 周）

**目标**：命令行单会话跑通

1. 对接 DeepSeek API（SDK 封装）
2. 写核心技能文件 v1（按第五章结构）
3. CLI 脚本：构建 prompt → 调用 AI → 保存输出
4. 手动运行 3-5 个目标
5. **迭代核心技能文件**（最重要的一步）

**交付物**: 1 个脚本 + 1 个技能文件 + 实测反馈

### 阶段二：平台化（2-4 周）

**目标**：Web 平台多会话

1. FastAPI + SQLite 后端
2. 会话管理 + 并发控制
3. 终态判定（12 条规则）
4. EventBus + WebSocket
5. Vue 3 仪表盘前端
6. 场景配置系统

**交付物**: 可多用户使用的 Web 平台

### 阶段三：加固（4-8 周）

**目标**：生产级稳定运行

1. 磁盘防护 + API 健康监控
2. 性能监控渐进降级
3. 优先级调度 + 自动重连
4. 报告去重 + 邮件通知
5. 前端完善（批量操作、导出）
6. 压力测试 + 长时间运行验证

**交付物**: 可 7×24 运行的生产系统

---

## 15. 项目目录结构

```
ai-vuln-system/
├── pyproject.toml
├── .env.example
│
├── src/
│   ├── main.py                   # FastAPI 应用入口
│   ├── config.py                 # 配置加载
│   │
│   ├── api/
│   │   ├── routes_sessions.py    # 会话 CRUD
│   │   ├── routes_reports.py     # 报告查询
│   │   ├── routes_settings.py    # 设置管理
│   │   └── websocket_handler.py # WebSocket 端点
│   │
│   ├── engine/
│   │   ├── scheduler.py          # 优先级队列 + 并发控制
│   │   ├── ai_worker.py          # 会话主循环 (DeepSeek)
│   │   ├── prompt_builder.py     # System Prompt 构建
│   │   ├── tool_executor.py      # Tool Call 执行器
│   │   ├── termination_detector.py # 12条规则判定
│   │   └── report_indexer.py     # 报告扫描/解析/入库/通知
│   │
│   ├── safety/
│   │   ├── disk_guard.py         # 磁盘配额 + 自循环检测
│   │   ├── api_health.py         # API 健康监控
│   │   ├── perf_monitor.py       # 性能渐进降级
│   │   └── orphan_cleaner.py     # 孤儿进程回收
│   │
│   ├── event_bus.py              # 发布订阅 (asyncio.Queue)
│   ├── database.py               # SQLite + aiosqlite + WAL
│   └── models.py                 # Pydantic 模型
│
├── skills/
│   └── core-skill.md             # 核心技能文件
│
├── scenarios/
│   ├── edu-rules.md
│   ├── src-rules.md
│   └── custom-rules.md
│
├── data/
│   ├── db.sqlite3
│   ├── sessions/{session_id}/
│   └── reports/{session_id}__{title}.md
│
├── frontend/                     # 阶段二加入
│   ├── index.html
│   ├── src/
│   │   ├── App.vue
│   │   ├── pages/
│   │   │   ├── Dashboard.vue
│   │   │   ├── SessionList.vue
│   │   │   ├── SessionDetail.vue
│   │   │   ├── Reports.vue
│   │   │   └── Settings.vue
│   │   └── composables/
│   │       ├── useWebSocket.ts
│   │       └── useApi.ts
│   └── package.json
│
├── cli/
│   └── avs.py                    # 命令行入口 (MVP 阶段主力)
│
└── tests/
    ├── test_scheduler.py
    ├── test_termination.py
    ├── test_tool_executor.py
    └── test_safety.py
```

---

## 16. 迭代哲学

系统上线后唯一重要的事：**迭代核心技能文件**。

| AI 行为 | 迭代动作 |
|---------|---------|
| 报了噪音 | 加到垃圾洞清单 |
| 做了危险操作 | 加到铁律 |
| 报告质量差 | 强化七问验证门 |
| 在死角死磕 | 调整时间约束 |
| 新的噪音模式 | 加到速查卡 |

每次实战 → 分析产出 → 更新那一个文件 → **这个文件就是核心竞争力**。

---

## 附录：设计原则总结

| 原则 | 说明 |
|------|------|
| 边界而非方法论 | 告诉 AI 什么不该做，不教它怎么做 |
| 一个文件够了 | 150-200 行，简洁有力 |
| 信任 AI 的能力 | 它知道怎么测试，你只管边界 |
| 垃圾洞清单置顶 | AI 第一眼看到的就是不该报什么 |
| 七问验证门 | 每个报告都必须通过终极过滤 |
| 决策树不是流程 | 动态分支，20 分钟无进展换方向 |
| 防遗忘 | 速查卡 + Phase 检查点 |
| 证据优先 | 物理证据 > AI 声明 |
| 持续迭代 | 每次实战都是优化机会 |
