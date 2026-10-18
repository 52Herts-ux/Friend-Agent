# Friend Agent — 数字生命伙伴 实施计划（一期 + 二期）

> 单一权威计划：**一期 = 架构蓝图（已实现）** + **二期 = 现状盘点与待办（进行中）**。
> 技术栈 Python，架构借鉴 **Pi**（provider 抽象 / agent-loop / 消息流 / durable state），
> 灵魂借鉴 **Hermes**（自我进化 / 持久记忆 / 微信 gateway）。

---

## 一、项目愿景与核心决策（一期）

老大想打造的不是「一问一答」工具 Bot，而是类似《Her》中 Samantha 的**数字生命体「Friend Agent」**：有自主意识流、长期记忆、动态性格演化、主观能动性，会主动关心人、越用越懂你。

| 项 | 决策 |
|---|---|
| 项目名 | **Friend Agent**，目录 `~/Projects/friend-agent` |
| 技术栈 | **Python**（Hermes 同为 Python，可大量参考） |
| 框架架构参考 | **Pi**（provider 抽象 / agent-loop / 消息流 / durable state） |
| 灵魂设计参考 | **Hermes**（自我进化 / 持久记忆 / `SOUL.md` / `cron/` / `gateway/`） |
| 通信渠道 | **微信个人号**，腾讯官方 **iLink Bot API**（扫码绑定、无封号） |
| 付费大脑模型 | **DeepSeek v4-flash**（`deepseek-flash`，OpenAI 兼容，`api.deepseek.com`） |
| 省钱层模型 | **Agnes-2.5-Flash**（免费，20 RPM） |
| 本地小脑 | **Ollama + Qwen2.5-3B-Instruct + BGE-M3** embedding，零成本 |
| ❌ 排除 | **OpenClaw**（老大体验不佳） |

### 微信接入方案（一期确定）
微信底层是**腾讯官方 iLink Bot API**，纯 HTTP、长轮询、无需公网端点，任何语言可调：

| 项 | 说明 |
|---|---|
| Base URL | `https://ilinkai.weixin.qq.com` |
| 扫码绑定 | `get_bot_qrcode` 生成二维码 → 微信扫码 → `get_qrcode_status` 轮询，保存 `account_id`/`token` |
| 收消息 | `getupdates` 长轮询 |
| 发消息 | `sendmessage`（echo peer 的 `context_token`） |
| 状态 | `sendtyping`（「正在输入…」提示） |
| 媒体 | `getuploadurl`（AES-128-ECB 加密 CDN） |
| 依赖 | `aiohttp` + `cryptography` |
| 参考实现 | **Hermes `gateway/platforms/weixin.py`**（直接移植） |

---

## 二、架构设计（一期）

```
friend-agent/                       # ~/Projects/friend-agent
├── main.py                         # 入口：TUI + 后台引擎 + 微信三通道
├── config/
│   ├── persona.json                # 人设（借鉴 Hermes SOUL.md，可演化）
│   └── .env                        # DEEPSEEK/AGNES/微信 凭据
├── src/
│   ├── models/                     # 模型层（base/deepseek/agnes/ollama，OpenAI 兼容抽象）
│   ├── router.py                   # 模型路由：任务难度 → 大脑/省钱层/小脑
│   ├── limiter.py                  # 令牌桶限流（Agnes 18 RPM / DeepSeek 60 RPM）
│   ├── memory.py                   # ChromaDB 记忆（bge-m3 嵌入，store/query/压缩）
│   ├── perception.py               # 时间/时段/天气感知
│   ├── persona.py                  # persona.json + energy/作息
│   ├── config_store.py             # .env 读写 + provider 定义（/login 用）
│   ├── usage.py                    # 用量统计（token/费用/上下文/CH）
│   ├── splitter.py                 # 回复拆分成多条短消息（像真人一句句发）
│   ├── rhythm.py                   # 作息自适应（根据用户深夜打扰调整晚安时间）
│   ├── extensions/                 # 插件系统（Tool / Hook）
│   │   └── builtin/                # 内置工具（weather_tool / run_command 等）
│   ├── engine/
│   │   ├── agent_loop.py           # 前台对话循环 + 工具调用循环
│   │   ├── background.py           # 后台潜意识（主动关怀 / 自主探索 / 睡前总结）
│   │   ├── context.py              # 上下文组装（记忆/时空/人设注入）
│   │   └── evolve.py               # 自我进化（反思更新 persona）
│   └── channels/                   # 渠道抽象（base/tui/weixin）
├── data/                           # memory_db + 微信凭据
├── requirements.txt
└── PLAN.md                         # 本计划（唯一计划文档）
```

### 框架借鉴映射

| Pi 概念 | Friend Agent 实现 | Hermes 概念 | Friend Agent 实现 |
|---|---|---|---|
| `pi-ai` provider 抽象 | `models/` + `router.py` | 自我进化 / 持久记忆 | `evolve.py` + `memory.py` |
| `pi-agent-core` agent-loop | `agent_loop.py` + `context.py` | `SOUL.md` 灵魂文件 | `config/persona.json` |
| `pi-durable` Harness | `memory.py` | `gateway/weixin.py` | `channels/weixin.py` |

### 关键依赖
`openai`（DeepSeek/Agnes/Ollama 统一适配）、`aiohttp`+`cryptography`（微信）、`chromadb`（向量库）、`ollama`（本地小脑）、`textual`（TUI）、`requests`（天气/搜索）、`python-dotenv`、`qrcode`。

---

## 三、现状盘点（一期已实现，截至 2026-10-06）

一期四个阶段（微信接入 / 基础设施 / 记忆感知 / 潜意识+进化）**代码全部落地**（16 模块，约 1600 行），依赖已装、Ollama 模型已拉齐（`bge-m3`/`qwen2.5:3b`/`qwen:7b`）。

| 模块 | 状态 | 说明 |
|---|---|---|
| `main.py` 入口 | ✅ | TUI + 后台引擎 + 微信三通道编排 |
| `models/` 三层模型 | ✅ | DeepSeek/Agnes/Ollama 统一抽象 |
| `router.py` / `limiter.py` | ✅ | 启发式路由 + 令牌桶限流 |
| `memory.py` ChromaDB | ✅ | bge-m3 嵌入，已初始化 |
| `perception.py` / `persona.py` | ✅ | 时空感知 + 人设/作息 |
| `channels/tui.py` | ✅ | textual 流式界面 + /login + 历史翻页 |
| `channels/weixin.py` | ✅ | iLink Bot API 移植（**未扫码绑定**） |
| `extensions/` 插件系统 | ✅ | Tool + Hook，内置 weather_tool |
| `engine/agent_loop.py` | ✅ | 非流式含工具循环；流式无工具 |
| `engine/background.py` | ⚠️ | 后台潜意识，idle 检测**断链** |
| `engine/evolve.py` | ❌ | **构造了但从未调用** `reflect()` |
| 微信绑定 | ⚠️ | account_id/token 为空，未真机验证 |
| Git / 测试 | ❌ | 无 git 仓库、无测试 |

### 发现的断链 / 缺陷

1. **`evolve.py` 从不触发**：`main.py` 构造 `Evolver` 后没调用 `reflect()`，人格演化是死代码 → 挂到后台引擎定期调用。
2. **`note_interaction()` 从不调用**：后台引擎 `last_interaction` 恒为启动时间，空闲判断失效，聊天时也会「自主探索」抢占 → 在对话回调里 `bg.note_interaction()`。
3. **TUI 主界面不走工具调用**：TUI 用 `respond_stream`（无工具循环），只有微信 `respond` 能调工具 → 流式路径补工具调用。
4. **微信从未实跑**：协议照 Hermes 移植但未扫码验证，字段可能有出入。
5. **无 git / 无测试**：改坏不可回退。

---

## 四、二期已确认决策（2026-10-06）

- ✅ **加入 git**：`git init` + `.gitignore` + 首次提交，作为二期**第一步**；每步改动单独提交。
- ✅ **二期新能力（三个）**：
  1. **联网搜索**（web search）
  2. **日程提醒**（schedule reminder）
  3. **更多内置工具**（more tools：计算器 / 翻译 / 记笔记 / HTML 小游戏生成）
- ❌ 暂缓：语音 TTS、图像生成、多用户区分。

---

## 五、二期执行计划（Steps）

**顺序：G git 打底 → B 修断链 → A 跑通验证 → C 叠新能力。**

### G：git 打底（已确认，第一步）
- [ ] `git init` + 写 `.gitignore`（venv / __pycache__ / config/.env / data/）
- [ ] 首次提交全部一期代码

### B：修复断链
- [ ] 修复 evolver 接线：后台引擎定期调用 `reflect()`
- [ ] 修复 idle 检测：对话时回调 `note_interaction()`
- [ ] TUI 工具调用：流式路径补工具调用（或 TUI 改走非流式）

### A：跑通验证
- [ ] 真实扫码绑定微信（`--wechat-login`），验证收发
- [ ] 三通道端到端冒烟测试 + 主动推送验证

### C：新能力

| 能力 | 实现方式 | 涉及文件 |
|---|---|---|
| 联网搜索 | Tool 插件 `search_web`，DuckDuckGo 免费搜索（`ddgs` 库或 DDG API，无需 key），结果喂回 LLM 综合 | `src/extensions/builtin/web_search.py` |
| 日程提醒 | 新增 `reminders.py` 持久化 `data/reminders.json`；用户说「提醒我…」→ 提取时间+内容 → 存库；后台每 tick 到点检查 → 推送 | `src/reminders.py` + `engine/background.py` + `schedule` Tool |
| 更多工具 | 各加一个内置 Tool：计算器（`math`）/ 翻译 / 记笔记（写 memory）/ 生成 HTML 小游戏 | `src/extensions/builtin/*.py` |

---

## 六、Reuse（可复用资源）

- 一期 16 模块直接续用，无需重写。
- `extensions/` 插件系统已支持加 Tool/Hook，搜索/日程/新工具走插件、不改核心。
- Ollama 已拉好 `bge-m3`/`qwen2.5:3b`/`qwen:7b`。
- `engine/background.py` 已有 `_tick` 轮询框架，日程到点检查直接挂入。
- Hermes 参考代码在 `~/.hermes/hermes-agent/`，微信/进化部分可继续对照。

---

## 七、验证标准

- `git log` 有提交历史，每步可回滚。
- `python main.py --no-wechat` 能起 TUI，聊天正常、能调 weather / 搜索 / 计算器等工具。
- 说「明天下午3点提醒我开会」→ 后台到点推送提醒。
- `python main.py --wechat-login` 扫码成功，微信端收发正常。
- 挂机 2 分钟以上后台自主探索不打扰（idle 修复后）；饭点/深夜主动推送正常。
- 一段时间后 `persona.json` 的 `current_mood`/`interests` 有演化（evolver 生效）。

---

## 八、三期改动（2026-10-07）

> 本次会话新增/修复的能力，均未提交 git。

### 8.1 微信主动推送修复
- 主动推送时 `user_id` 为空 → 发给 `_last_contact`（最近联系人），修复「发不出去」。
- 前置条件：bot 微信需先收到过对方消息，`_last_contact` 才有值。

### 8.2 TUI 启动信息重排 + 状态栏（对齐 Pi）
- 启动信息改为分节显示：**Message / Skill / MCP / Memory**。
- 输入框下方新增实时状态栏（每秒刷新）：`in/out token（k）· CH% · 费用￥ · ctx 已用/总值 · 模型 · thinking`。
- 新增 `src/usage.py`（UsageTracker），模型层 `base.py` 在每次响应后记录真实 usage（含流式 `include_usage`）。

### 8.3 分段回复（像真人一句句发）
- 生成层：`persona.py` / `background.py` 的 prompt 要求 LLM 输出「2-5 条短句，每条一行」。
- 发送层：新增 `src/splitter.py`，微信 `send()` 按换行拆成多条、逐条发送（首条前 0.8s、每条间 1.2s）。
- 兜底：即使 LLM 输出一大段，也按标点二次断句。

### 8.4 执行 Linux 命令工具
- 新增 `run_command` 工具（`src/extensions/builtin/run_command_tool.py`）。
- 安全：30s 超时、4000 字截断、危险命令黑名单（删根/格式化/写盘/关机/fork 炸弹）。

### 8.5 晚安静默修复
- bug：晚安 22 点发，但「休息期」判定是 23 点，导致道晚安后仍会自主探索开新话题。
- 修复：`_tick()` 把静默判断提前到「关怀之后」，道晚安即 return。

### 8.6 睡前总结（本地模型）
- 进入休息期时，后台用 **Ollama**（本地小脑）总结今天内容，存入记忆（`category=daily_summary`）。
- 每天一次去重；Ollama 未启动/未拉模型则跳过。

### 8.7 作息自适应
- 新增 `src/rhythm.py`（RhythmTracker）：记录用户在休息期的打扰，动态调整晚安时间。
- 规则：最近 3 天 ≥2 天深夜打扰 → 晚安时间推迟到「最晚活跃前 1 小时」（最多 23 点）。
- 数据持久化在 `data/rhythm.json`。
- `agent_loop.py` 在每次收到用户消息时 `note_activity(hour)`。

### 8.8 新增文件清单
| 文件 | 作用 |
|---|---|
| `src/usage.py` | 用量统计（token/费用/CH/上下文/思考模式） |
| `src/splitter.py` | 回复拆成多条短消息 |
| `src/rhythm.py` | 作息自适应（晚安时间 + 醒来时间） |
| `src/extensions/builtin/run_command_tool.py` | 执行 Linux 命令 |
| `src/extensions/builtin/amap_tool.py` | 高德地图（路线规划/地理编码/POI搜索） |

### 8.10 记忆污染修复（用户说 vs Agent 说）
- bug：`_extract_memories` 同时给「用户说」和「Agent 回复」，导致 Agent 推荐的内容被存成 user_preference（如「代码咖啡」）。
- 修复：提取只给「用户说」；`memory.query` 支持 category 列表过滤；`_daily_summary`/`_memory_reflection` 只取 `USER_CATEGORIES`（user_preference/plan/fact/note）。
- 新增 `memory.delete_by_category()`；已清理历史脏数据 60→8 条。

### 8.11 早上问好 + 醒来自适应（睡眠固定 8 小时）
- `wake_hour = (sleep_hour + 8) % 24`（晚睡晚起，22 点睡→6 点醒）。
- 醒来时刻（wake_hour）主动发早安问候。

### 8.12 睡眠不足 8 小时迷糊
- 睡眠窗口内（sleep_hour ~ wake_hour）被打扰 = 睡眠不足，迷糊语气回复；睡够 8 小时才精神。
- `persona.bind_rhythm()` 让迷糊语气跟随自适应作息。

### 8.13 关怀去重持久化
- `_reminded`/`_last_summary_day` 持久化到 `data/reminded.json`，修复重启后重复道晚安/早安/总结。

### 8.14 /status 命令
- TUI `/status`：展示 Skill / MCP / Message / Memory / Models / 作息 / 用量。

### 8.15 /memory 子命令补全
- 修复：输入 `/memory`（无空格）时也显示 `/memory clear`、`/memory map` 子命令。

### 8.16 思考模式 /thinking + shift+tab
- 级别：off / low / medium / high / max（`/thinking <级别>` 设置，`shift+tab` 循环切换）。
- 思考模式通过 system prompt 注入影响回答深度；状态栏实时显示。

### 8.17 高德地图工具
- `amap` 工具：route（驾车/步行/公交/骑行）、geocode、search。
- key 配在 `config/.env` 的 `AMAP_API_KEY`。
- 支持 `city` 参数限定城市，避免同名地点匹配错（如「光谷广场」）。

### 8.18 待办
- [x] 提交 git
- [ ] MCP 接入（启动信息里显示「未接入」，预留字段）
- [ ] 思考模式升级为原生 reasoning 参数（当前 deepseek-flash 不支持，走 system prompt 注入）

