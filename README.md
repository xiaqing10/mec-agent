# 智慧交通垂域智能体

MEC边缘计算设备的AI诊断与监控系统，基于 LangGraph Agent 框架。

---

## 技术架构

```
Web UI (webui.py) / API Client
        ↓
  server.py (aiohttp, 端口 8645)
        ↓
  handlers/ (auth/chat/feedback/memory/repair)
        ↓
  agent.py (LangGraph StateGraph)
   节点: agent → tools → update_context → feedback → END
   持久化: AsyncSqliteSaver → checkpoints.db
        ↓                    ↓
  tools/ 包 (23个 Tool)    diagnose_mec/ 包 (SSH诊断引擎)
   ├── tool_device          ├── diagnostics.py
   ├── tool_project         ├── parsers.py
   ├── tool_db (MySQL)      └── ssh.py
   ├── tool_ssh
   ├── tool_dingtalk
   ├── tool_fetch
   ├── tool_help
   ├── tool_memory
   ├── tool_repair
   ├── tool_image
   └── tool_mongodb ★新增★ — 雷达交通数据工具

外部数据源:
  火山引擎 LLM (deepseek-v4-flash)  飞书 API (监控报告)
  MySQL (mec_monitor, 设备/传感器)   钉钉 Webhook (告警推送)
  MongoDB (radarData, 交通流量/事件) ★新增★
```

---

## 模块说明

### 核心层

| 文件 | 说明 |
|------|------|
| `server.py` | aiohttp Web 服务入口，含认证中间件，注册所有 API 路由 |
| `agent.py` | LangGraph Agent 定义：AgentState、StateGraph（agent/tools/update_context/feedback 节点）、system prompt（32条规则）、AsyncSqliteSaver 持久化 |
| `config.py` | 全局配置：LLM API（火山引擎 deepseek-v4-flash）、MySQL 连接、SSH 密钥路径、用户列表、飞书/钉钉 API 密钥、ContextVar 当前用户 ID |
| `tools.py` | 兼容性包装，重新导出 `tools/` 包的 `TOOLS` 列表 |

### handlers/ 包 — API 请求处理

| 文件 | 说明 |
|------|------|
| `auth.py` | 登录/登出/获取当前用户（Cookie-based session） |
| `chat.py` | 聊天核心：`handle_chat`（非流式）、`handle_chat_stream`（SSE 流式，9种事件类型）、`handle_raw_diagnose`（直接工具调用） |
| `feedback.py` | 反馈 CRUD：提交评分、统计、列表、更新、删除、置顶（管理员） |
| `memory.py` | 用户记忆 API：列表、摘要（含容量）、创建、更新、删除 |
| `repair.py` | 修复执行：接收前端确认的修复操作，调用 `execute_repair()` |

### tools/ 包 — LangChain Tool 定义（23个）

| 文件 | 工具 | 说明 |
|------|------|------|
| `tool_device.py` | `diagnose_device` | 单设备 6 维度 SSH 诊断（物理机/容器/进程/ROS/数据源/传感器） |
| | `device_info` | 设备详细指标查询（硬盘/内存/CPU/网络/运行时间/历史数据） |
| | `llm_diagnose_device` | SSH 采集全部原始数据 + LLM 深度根因分析 |
| `tool_project.py` | `diagnose_project` | 批量诊断项目下所有异常设备（优先从数据库获取，回退飞书报告） |
| | `analyze_logs` | 分析监控日志，P0-P3 分级，历史对比 |
| | `llm_analyze_logs` | LLM 深度分析日志 |
| `tool_db.py` | `query_abnormal` | 查询异常设备统计 |
| | `query_device_from_db` | MySQL 查询单台设备状态（无需 SSH，离线也能查历史记录） |
| | `query_project_from_db` | MySQL 查询整个项目状态 |
| `tool_ssh.py` | `ssh_exec_command` | 执行单个 SSH 只读命令（仅用于细粒度查询，不可替代 diagnose_device） |
| `tool_dingtalk.py` | `push_to_dingtalk` | 推送消息到钉钉 |
| `tool_fetch.py` | `fetch_report` | 获取飞书监控报告原文 |
| `tool_help.py` | `help_info` | 使用帮助 |
| `tool_memory.py` | `memory` | Agent 可调用的用户记忆管理（add/replace/remove/list） |
| `tool_repair.py` | `repair_device` | 安全修复操作（重启容器/进程/服务、清理缓存/日志/临时文件），需用户前端确认 |
| `_shared.py` | — | 共享工具函数：进度回调、日志错误摘要、诊断结果格式化、根因中文翻译 |
| `tool_image.py` | `query_event_records` | 从 MySQL 查询事件记录列表 |
| | `query_project_event_stats` | 查询项目事件统计汇总 |
| | `fetch_event_image` | 从远程设备抓取事件图片 |
| `tool_mongodb.py` ★新增★ | `query_traffic_flow` | 查询断面流量（车流量/平均速度/时间占有率） |
| | `query_events` | 查询雷达事件记录（事件类型/车牌/车速/车道） |
| | `query_event_stats` | 按事件类型统计分布 |
| | `query_device_metrics` | 查询设备运行健康指标（CPU/内存/磁盘/温度/告警） |
| | `analyze_traffic_pattern` | 综合交通流时间序列分析 |
| | `traffic_analysis_report` | LLM二次分析报告（总结/异常/预测/关联） |

### diagnose_mec/ 包 — SSH 诊断引擎

| 文件 | 说明 |
|------|------|
| `diagnostics.py` | 核心诊断函数：`diagnose_container_offline`（4步：物理机→Docker→docker exec→容器SSH）、`diagnose_zero_images`（5步：连通性→采集→supervisor分析→日志检查→rostopic频率）、`collect_device_raw_data`（LLM深度分析用） |
| `parsers.py` | 解析函数：`_parse_ssh_failure_reason`、`_parse_supervisor_status`、`_format_abnormal_summary`、`_load_diagnostic_patterns` |
| `ssh.py` | SSH 连接管理：`ssh_exec`（paramiko 密码 + 系统 ssh 公钥）、`find_physical_user`（多用户+多认证方式尝试）、`_combined_ssh`（批量采集）、`_docker_exec_cmd`（fallback） |

### 数据分析层

| 文件 | 说明 |
|------|------|
| `mec_analyze.py` | 从飞书 API 拉取 MEC 监控报告，按关键词"全局刷新完成报告"过滤，支持 token 认证、重试、时间戳去重、`--update-timestamp` 模式 |
| `code_analyze.py` | 解析飞书报告为结构化数据，P0-P3 分级，历史对比（持续/新增/恢复/恶化/好转），基于持续时长动态升级优先级，自动推钉钉，结果保存至 `diagnose_logs/` |
| `diagnose_project.py` | 优先从数据库获取项目异常设备列表，数据库无数据时回退到飞书报告，逐台 SSH 诊断，汇总推钉钉 |
| `query_sensor_status.py` | 从 MySQL 查询设备关联的摄像头/雷达在线状态，支持设备名/IP 查找，含设备数据库信息查询 |
| `dingtalk_send.py` | 钉钉机器人 Webhook 推送，HMAC-SHA256 签名认证 |

### 存储层

| 文件 | 类型 | 说明 |
|------|------|------|
| `checkpoints.db` | SQLite | LangGraph 对话状态持久化（AsyncSqliteSaver），按 thread_id（session_id）隔离 |
| `feedback.db` | SQLite | 用户反馈记录（意图、工具操作、评分、自评分数、置顶），由 `feedback_store.py` 管理 |
| `user_memory.db` | SQLite | 用户记忆存储（偏好/习惯/事实），LLM 自动提取，容量管理，由 `user_memory_store.py` 管理 |
| `mec_structured_history.json` | JSON | 飞书报告结构化历史，最多 30 条 FIFO |
| `last_check.json` | JSON | 上次飞书报告检查时间戳 |
| `diagnostic_patterns.json` | JSON | 诊断模式配置（驱动异常/ROS异常/OOM等） |
| `diagnose_logs/project_history/*.json` | JSON | 各项目诊断历史记录 |
| `repair_logs/*.jsonl` | JSONL | 修复操作审计日志（按天分文件） |

### 前端

| 文件 | 说明 |
|------|------|
| `webui.py` | 内嵌 HTML/CSS/JS 单页应用：聊天界面、会话管理（侧边栏）、登录认证、反馈评价、用户记忆管理、修复确认弹窗、指南面板、SSE 流式渲染 |

---

## LangGraph 架构

### StateGraph 节点

| 节点 | 功能 |
|------|------|
| `agent` | LLM 决策节点：注入 system prompt（32条规则）+ 用户记忆 + 对话上下文，决定调用工具或直接回复 |
| `tools` | ToolNode：执行 agent 选中的工具（15个），返回结果 |
| `update_context` | 从工具结果中提取 `last_ip` / `last_project`，更新对话状态 |
| `feedback` | 提取对话意图，LLM 自评正确性分数（0-10），标记是否需要用户反馈 |

### 数据流

```
agent ──(有工具调用)──▶ tools ──▶ agent ──▶ ... ──▶ (无工具调用) ──▶ update_context ──▶ feedback ──▶ END
```

### 模型配置

- 模型: `deepseek-v4-flash`
- API: 火山引擎 Ark (`https://ark.cn-beijing.volces.com/api/coding/v3`)
- 参数: `temperature=0.1`, `max_retries=1`

### SSE 事件类型（handle_chat_stream）

| 事件 | 说明 |
|------|------|
| `info` | 初始化状态、Agent 初始化完成 |
| `token` | LLM 流式输出增量文本 |
| `tool_start` | 工具开始执行（含工具名和输入参数） |
| `tool_end` | 工具执行完成 |
| `tool_result` | 工具返回结果（截断至 8000 字符） |
| `diag_progress` | 诊断进度（仅 `diagnose_device`，含 name/status/detail，status: ok/error/warning/skip/progress） |
| `done` | 流式输出完成 |
| `error` | 错误信息 |
| `feedback_request` | 请求用户反馈（含 session_id、summary、intent） |

---

## 依赖

| 包 | 用途 |
|----|------|
| `langgraph` | LangGraph Agent 框架 |
| `langchain-core` | LangChain 基础抽象（Tool、Message） |
| `langchain-openai` | LLM 客户端（OpenAI 兼容协议） |
| `langgraph-checkpoint-sqlite` | SQLite 检查点持久化 |
| `aiohttp` | 异步 Web 服务 |
| `pymysql` | MySQL 查询（设备/传感器状态） |
| `pymongo` | MongoDB 查询（雷达交通数据：流量/事件/设备指标） |
| `paramiko` | SSH 远程设备诊断 |
| `openai` + `httpx` | LLM API 调用 |
| `bcrypt` | 用户密码认证 |
| 钉钉机器人 Webhook | 告警推送 |
| 飞书 API | 监控报告获取 |

完整依赖见 `requirements.txt`。

---

## 启动

```bash
# API Server + Web UI（端口 8645）
python3 server.py

# LangGraph Studio（开发/调试）
./start_langgraph_studio.sh
```

---

## 配置

配置信息在 `config.py` 中，支持环境变量覆盖。主要配置项：

- `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` — LLM API
- `MYSQL_*` — MySQL 数据库连接
- `MONGO_*` — MongoDB 数据库连接（雷达交通数据）
- `SSH_KEY_PATH` — SSH 密钥路径
- `USERS` — 用户账号列表
- `FEISHU_*` — 飞书 API
- `DINGTALK_*` — 钉钉 Webhook

---

## Agent System Prompt 规则

### 可用工具

| 工具 | 说明 |
|------|------|
| `diagnose_device` | 诊断单台设备（SSH 远程检查物理机、容器、进程、ROS、数据源、传感器 6 个维度） |
| `diagnose_project` | 批量诊断项目下所有异常设备 |
| `device_info` | 查询设备详细指标（硬盘、内存、CPU、网络、运行时间、历史数据） |
| `analyze_logs` | 分析监控日志，P0-P3 分级 |
| `llm_analyze_logs` | LLM 深度分析日志 |
| `llm_diagnose_device` | SSH 采集设备全部原始数据 + LLM 深度根因分析（根因/影响范围/修复建议/预防措施） |
| `fetch_report` | 获取最新监控报告原文 |
| `query_abnormal` | 查询异常设备统计 |
| `push_to_dingtalk` | 推送消息到钉钉 |
| `ssh_exec_command` | 执行单个 SSH 只读命令（仅用于 diagnose_device 和 device_info 不覆盖的细粒度查询） |
| `help_info` | 帮助信息 |
| `query_device_from_db` | 从 MySQL 数据库查询单台设备状态（无需 SSH，即使设备离线也能查到历史记录） |
| `query_project_from_db` | 从 MySQL 数据库查询整个项目状态（无需飞书报告） |
| `memory` | 管理用户记忆（add/replace/remove/list），可主动保存重要偏好、习惯或事实 |
| `repair_device` | 安全修复操作（重启容器/进程/服务、清理缓存/日志/临时文件），需用户在前端弹窗确认后才执行 |
| `query_event_records` | 从 MySQL 查询事件记录列表（支持多条件过滤：项目/设备/日期/事件类型/车牌） |
| `query_project_event_stats` | 查询项目事件统计汇总（按类型/设备分类） |
| `fetch_event_image` | 根据事件记录 ID 从远程设备抓取事件图片并返回 URL |
| `query_traffic_flow` ★新增★ | 查询断面流量（车流量/平均速度/时间占有率/道路状态），数据源：MongoDB radarData.flowStat |
| `query_events` ★新增★ | 查询雷达事件记录（事件类型、车牌、车速、车道、方向），数据源：MongoDB radarData.event |
| `query_event_stats` ★新增★ | 按事件类型统计分布（违章/拥堵/异常分类汇总），数据源：MongoDB radarData.event |
| `query_device_metrics` ★新增★ | 查询 MEC 设备运行健康指标（CPU/内存/磁盘/温度/告警），数据源：MongoDB radarData.metric |
| `analyze_traffic_pattern` ★新增★ | 综合交通流时间序列分析（高峰识别、拥堵分析），数据源：MongoDB radarData.flowStat |
| `traffic_analysis_report` ★新增★ | LLM 二次分析报告（总结/异常识别/流量预测/事件-流量关联），数据源：MongoDB 多集合聚合 |

### 规则列表

| # | 规则内容 |
|---|---------|
| 1 | 用户说"看/查看/怎么样/情况/状态/有无/多少/统计"表示只读，先查再回答 |
| 2 | 用户说"诊断/排查/检查原因/修/恢复"表示要执行操作 |
| 3 | 用户指定了 IP 或设备名时，隐含诊断意图 |
| 4 | 如果用户问"这台设备的内存/硬盘"等且没有指定 IP，检查对话历史中最近操作的设备 |
| 5 | 不要假设设备状态，调用工具获取真实数据 |
| 6 | 对于闲聊或问候，直接友好回复，不需要调用工具 |
| 7 | 回答要简洁专业，用中文 |
| 8 | `ssh_exec_command` 用于执行单个只读命令（cat/tail/ls/ps/grep/df 等），仅在 `diagnose_device` 和 `device_info` 不覆盖的特定细粒度场景下使用（如查看特定日志文件、特定配置文件内容）。**严禁用 `ssh_exec_command` 替代 `diagnose_device` 或 `device_info` 进行多维度诊断** |
| 9 | 当用户要求对某台设备进行诊断、排查、检查问题、查看状态（包括"帮我看下"、"怎么样"、"有什么问题"、"什么情况"、"查一下"等隐含诊断意图的表述），或指定了 IP/设备名并期望了解设备整体状况时，**必须优先调用 `diagnose_device`**（一次调用完成 6 维度全面检查）。`diagnose_device` 是高聚合工具，远比逐个调用 `ssh_exec_command` 高效，**严禁用 `ssh_exec_command` 替代** |
| 10 | 当用户问设备详细信息（硬盘、内存、CPU 等）时，使用 `device_info` 工具。`device_info` 也是一次调用完成多个指标查询，**不要用 `ssh_exec_command` 逐个命令替代** |
| 11 | 当用户想看 `diagnose_device` 和 `device_info` 不覆盖的特定日志文件、特定配置文件内容等细粒度查询时，才使用 `ssh_exec_command` |
| 12 | `ssh_exec_command` 的 `ros_env` 参数控制是否需要 ROS 环境初始化。涉及 rostopic/rosnode/rosservice 等 ROS 命令时必须传 `ros_env=True` |
| 13 | 当 `diagnose_device` 返回诊断结果后，**必须将 6 个维度（物理机、容器、进程、ROS、数据源、传感器）的完整结果展示给用户**，不要遗漏任何维度，不要重新组织成其他格式。即使某些维度为"skip"状态也要展示，让用户全面了解设备状况 |
| 14 | 基本诊断（`diagnose_device`）后，如果 6 个维度中存在 error 状态但根因不明确（如进程日志显示未知错误、ROS topic 全部无数据但进程正常等），应主动调用 `llm_diagnose_device` 做 LLM 深度根因分析。如果 `diagnose_device` 已明确给出根因（如 docker_service_down、gpu_driver_error、dev_container_stopped 等），则直接给出结果和修复建议，不需要再做深度分析 |
| 15 | SSH 连接策略：系统会先尝试公钥登录，公钥失败后自动尝试数据库中的密码登录；如果都失败，会返回数据库中记录的历史状态信息 |
| 16 | 当诊断结果显示"数据库记录"时，说明该数据是历史快照，不是实时数据，回答时需要说明这一点 |
| 17 | 回复末尾不要输出任何数字评分或分数，不要附加无关的数字 |
| 18 | 当展示项目概览、核心指标、异常设备列表等统计数据时，必须使用标准 markdown 表格格式（以 `\|` 开头、有表头分隔行），不要使用 `│` 或 ASCII 画框的自定义表格 |
| 19 | `query_device_from_db` 从 MySQL 数据库查询设备状态，无需 SSH 连接。适用于：a)快速查看设备概况 b)设备离线时查看历史记录 c)批量了解设备状态。数据库数据不是实时的，需要说明数据更新时间 |
| 20 | `query_project_from_db` 从 MySQL 数据库查询项目状态，无需解析飞书报告。返回项目下所有设备的汇总统计和异常设备列表 |
| 21 | 数据源优先级：数据库（`query_device_from_db` / `query_project_from_db`）> 飞书报告（`analyze_logs` / `fetch_report` / `query_abnormal`）。除非用户明确说"从飞书/报告获取"，否则优先从数据库查询 |
| 22 | 优先用 `query_device_from_db` 查询设备基本状态（只读场景），需要深度诊断时再用 `diagnose_device`（SSH 实时检查） |
| 23 | markdown 表格：表头列数和数据行列数必须严格一致。表头分隔行（`\|---\|`）中每个列的 `-` 数量至少 3 个。确保每行 `\|` 的数量相同 |
| 24 | 表格第一行必须是表头，第二行必须是分隔行（`\|---\|`），之后才是数据行。不要在表格前后使用 ` ``` ` 代码块包裹表格 |
| 25 | 工具返回的结果中已经包含了数据来源和时间说明，直接呈现工具返回的内容即可，不需要自己补充"数据来源"或"数据说明" |
| 26 | 工具返回的 markdown 表格直接原样复制，不要自己重新生成表格。如果某些列全是 0 被工具自动过滤掉了，也不要去修改它。可以在表格后面附加文字说明 |
| 27 | 用户提到的项目名直接从数据库 `mec_device` 表的 `project` 字段获取，如德会、德会隧道、柯诸等。不要假设项目别名 |
| 28 | 对话中如果用户透露了重要偏好（如回复风格、关注项目）、习惯（如常查看的维度、常用操作）或个人背景信息，应主动调用 `memory` 工具保存（target=user），但不要为了保存而保存，只保存有长期价值的模式信息 |
| 29 | 诊断完成后，如果发现了可修复的问题（如容器停止、进程挂掉、磁盘空间不足），应主动调用 `repair_device` 工具生成修复方案。但不要自动执行，系统会生成方案供用户在前端确认 |
| 30 | `repair_device` 支持以下操作：restart_container（重启容器，需容器名）、restart_process（重启进程，需进程名）、restart_service（重启服务，需服务名）、clear_cache（清理内存缓存）、vacuum_journal（清理日志保留 200M）、clean_temp（清理 7 天前临时文件） |
| 31 | 修复操作是安全的：只重启不删除，清理操作有保留策略。不要建议白名单外的操作。每次修复后建议重新诊断验证效果 |
| 32 | **设备诊断标准流程**：a) 调用 `diagnose_device` 获取 6 维度诊断结果 → b) 完整展示所有维度 → c) 若根因明确直接给出修复建议（可调用 `repair_device`）；若根因不明确则调用 `llm_diagnose_device` 深度分析 → d) 深度分析后给出修复建议 |

### 诊断维度

| 维度 | 检查内容 |
|------|---------|
| 物理机 | SSH 可达性、运行时间、硬盘占用率（`/` 和 `/data`） |
| 物理机离线 | 飞书报告中的物理机离线设备（独立于容器/图片问题，优先级最高） |
| 容器 | Docker 运行状态、SSH 连接 |
| 进程 | supervisor 进程状态、日志错误分析（驱动异常/ROS连接失败/OOM） |
| ROS | roscore 运行状态、topic 频率 |
| 数据源 | 今日图片数量 |
| 传感器 | 摄像头和雷达在线率 |

---

## RAG 知识检索增强系统

### 功能说明

RAG (Retrieval-Augmented Generation) 系统通过向量检索技术，将历史诊断案例、运维知识、修复记录等注入到 LLM 的上下文中，提升诊断准确性和回复质量。

### 数据来源

| 数据源 | 路径 | 内容 |
|--------|------|------|
| 诊断历史 | `diagnose_logs/project_history/*.json` | 各项目设备的历史诊断记录 |
| 修复日志 | `repair_logs/repair_*.jsonl` | 修复操作记录（成功/失败） |
| 运维知识 | `knowledge/*.md` | 故障排查手册、运维文档 |
| 用户记忆 | `user_memory.db` | 用户偏好和习惯 |

### 工作流程

```
用户提问
    ↓
1. RAG 自动检索相关知识（最近7天，带时间衰减）
    ├── 历史诊断案例
    ├── 运维知识
    ├── 修复记录
    └── 用户记忆
    ↓
2. 检索结果注入 system prompt
    ↓
3. LLM 基于增强后的上下文生成回复
    ↓
4. 诊断完成后，有明确根因的结果自动入库
```

### 质量过滤规则

| 数据类型 | 入库条件 | 说明 |
|----------|----------|------|
| 诊断结果 | `root_cause` 不为空且不为 `unknown` | 只入库有明确根因的诊断 |
| 诊断结果 | `overall != "normal"` | 跳过正常设备的诊断 |
| 用户反馈 | `rating == "satisfied"` | 只入库用户满意的对话 |
| 修复日志 | `success == True` | 只入库成功的修复操作 |

### 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `RAG_ENABLED` | `true` | RAG 功能开关 |
| `RAG_EMBEDDING_MODEL` | `rag_data/chroma/text2vec-base-chinese` | Embedding 模型路径 |
| `RAG_TOP_K` | `5` | 默认返回结果数 |
| `RAG_MAX_DAYS` | `7` | 只检索最近7天的记录 |

### 使用方式

**自动检索**：每次用户提问时，系统自动检索相关知识注入上下文。

**主动查询**：LLM 可调用 `rag_search_knowledge` 工具：
```
搜索知识库中关于"图片为0"的解决方案
```

**手动导入知识**：
```bash
python import_knowledge.py
```

### 文件结构

```
rag/
├── __init__.py        # 模块初始化
├── config.py          # 配置（模型路径、检索参数）
├── embeddings.py      # Embedding 接口封装
├── retriever.py       # 检索器（时间衰减、状态过滤）
└── ingest.py          # 数据导入管道（质量过滤）
```

---

## 反馈系统

### 功能说明

反馈系统收集用户对 Agent 回复的评价，用于优化回复质量和 RAG 知识库。

### 反馈流程

1. 用户完成一轮对话后，前端弹出反馈窗口
2. 用户可选择评分（满意/部分满意/不满意）并填写文字反馈
3. 反馈记录保存到 `feedback.db`
4. 高评分反馈可作为 RAG 知识库的参考

### 反馈评分

| 评分 | 说明 |
|------|------|
| `satisfied` | 满意，回复准确有用 |
| `partial` | 部分满意，回复有帮助但不完整 |
| `unsatisfied` | 不满意，回复错误或无帮助 |

### 数据结构

```sql
CREATE TABLE feedback (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT,
    user_id TEXT,
    intent TEXT,
    actions TEXT,
    rating TEXT,
    feedback_text TEXT,
    auto_correctness INTEGER,
    created_at TEXT
);
```

---

## 交通数据分析

### 数据来源

| 数据源 | 集合 | 说明 |
|--------|------|------|
| 流量数据 | `flowStat` | 断面流量统计（5分钟粒度预聚合） |
| 事件数据 | `event` | 雷达事件记录（逆行/超速/停车等） |
| 设备指标 | `metric` | 设备运行健康指标（CPU/内存/磁盘/温度） |

### 流量查询规则

- **数据源**：`flowStat` 集合（5分钟粒度预聚合）
- **方向处理**：排除"双向"记录（双向=上行+下行，会导致重复计算）
- **时间字段**：`EndTime`（统计结束时间）
- **默认时间范围**：最近15分钟