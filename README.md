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
  tools/ 包 (26个 Tool)    diagnose_mec/ 包 (SSH诊断引擎)
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
| `agent.py` | LangGraph Agent 定义：AgentState、StateGraph（agent/tools/update_context/feedback 节点）、system prompt（工具优先策略）、MemorySaver 进程内会话状态（生产环境持久化计划后续切换） |
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

### tools/ 包 — LangChain Tool 定义（26个）

| 文件 | 工具 | 说明 |
|------|------|------|
| `tool_device.py` | `diagnose_device` | 单设备 6 维度 SSH 诊断（物理机/容器/进程/ROS/数据源/传感器） |
| `tool_context.py` | `resolve_mec_device` / `resolve_mec_project` | 确定性解析设备和项目，避免LLM猜测实体 |
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
| `agent` | LLM 决策节点：注入 system prompt（工具优先策略）+ 用户记忆 + 对话上下文，决定调用工具或直接回复 |
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

## Agent System Prompt 策略

Agent 不再在 System Prompt 中维护完整工具清单；**实际工具名称、参数和能力以 Tool Schema 为唯一事实来源**。Prompt 只负责路由原则和安全边界，避免工具数量/参数变更后出现提示词过期。

### 核心策略

| 类别 | 策略 |
|---|---|
| 实体解析 | 非IP设备名/编号先调用 `resolve_mec_device`；项目名/简称先调用 `resolve_mec_project`；多候选禁止猜测 |
| 上下文 | 当前消息明确指定的项目/设备优先；历史上下文只用于明确的“这个设备/该项目”等省略指代 |
| MEC诊断 | 实时设备诊断使用 `mec_diagnose_device`；项目批量诊断使用 `mec_diagnose_project` |
| 数据查询 | 已有数据库状态优先 `query_mec_*`；CPU/内存/硬盘等具体指标使用 `mec_device_info` |
| 细粒度SSH | 只有聚合工具无法覆盖的具体文件/日志/配置才使用 `mec_ssh_exec` |
| 深度分析 | 基础诊断明确需要进一步分析时才调用 `mec_llm_diagnose_device` |
| 数据源 | 默认使用MEC数据；只有明确的服务器/道路/交通流场景使用 `query_server_*` |
| 修复 | 用户明确要求修复后才调用修复工具；先生成方案，确认后执行 |
| 记忆 | 只有明确的长期偏好/事实才写入记忆；一次性查询不能成为长期偏好 |
| 结论 | 先工具、后事实总结；不得根据历史状态或模型常识猜测实时设备状态 |

### 诊断流程

```
用户请求
  ↓
实体解析（必要时 resolve_mec_device / resolve_mec_project）
  ↓
选择确定性查询/诊断 Tool
  ↓
获取实时或数据库事实
  ↓
若基础诊断明确需要深度分析 → mec_llm_diagnose_device
  ↓
输出：结论 → 关键证据 → 影响 → 建议
```

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