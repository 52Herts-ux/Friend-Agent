# Friend Agent 🫶

一个「数字生命伙伴」—— 有自主意识流、长期记忆、动态性格演化，会主动关心你、越用越懂你的 AI 朋友。

> 技术栈：Python。架构借鉴 **Pi**（provider 抽象 / agent-loop / 消息流 / durable state），
> 灵魂借鉴 **Hermes**（自我进化 / 持久记忆），微信接入腾讯官方 **iLink Bot API**（扫码绑定、无封号）。

## 特性

- 🧠 **三层模型路由**：DeepSeek（大脑）→ Agnes-2.5-Flash（省钱）→ Ollama 本地（小脑）
- 🗂️ **立体记忆**：ChromaDB 向量库 + 人设 persona.json
- 🔁 **双循环**：前台对话循环 + 后台潜意识循环（主动关怀 / 自主探索）
- 🌦️ **时空感知**：时间 + 天气注入 Prompt，饭点/深夜主动关心
- 📱 **多渠道**：微信（iLink Bot）+ TUI（textual），渠道抽象可扩展 Telegram 等
- 🧩 **插件系统**：Tool（工具）+ Hook（钩子）扩展，后续加能力不改核心
- 🌱 **自我进化**：定期反思更新人设（借鉴 Hermes）

## 快速开始

```bash
cd ~/Projects/friend-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 1. 配置密钥（两种方式任选）
# 方式一：TUI 里交互式配置（推荐，借鉴 Pi 的 /login）
./venv/bin/python main.py
#   在 TUI 里输入 /login → 方向键选 DeepSeek/Agnes → 输 API key → 回车保存

# 方式二：手动编辑
cp config/.env.example config/.env
# 编辑 config/.env，填入 DEEPSEEK_API_KEY、AGNES_API_KEY

# 2. 准备本地小脑（可选，用于后台/嵌入）
ollama pull qwen2.5:3b
ollama pull bge-m3

# 3. 扫码绑定微信（首次）
python main.py --wechat-login

# 4. 启动（TUI + 后台引擎 + 微信）
python main.py            # 仅 TUI + 后台（不接微信）用 --no-wechat
```

## 配置

见 `config/.env.example`。关键项：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 付费大脑（默认 `deepseek-flash`） |
| `AGNES_API_KEY` / `AGNES_MODEL` | 省钱层（默认 `agnes-2.5-flash`） |
| `OLLAMA_MODEL` / `OLLAMA_EMBED_MODEL` | 本地小脑 / 嵌入模型 |
| 微信凭据 | 扫码后自动写入 `data/weixin_account.json` |

人设文件：`config/persona.json`（可随对话演化）。

## 目录结构

```
friend-agent/
├── main.py                    # 入口
├── config/                    # .env + persona.json
├── src/
│   ├── models/                # 模型层（provider 抽象，借鉴 Pi）
│   ├── router.py              # 模型路由
│   ├── limiter.py             # 令牌桶限流
│   ├── memory.py              # ChromaDB 记忆
│   ├── perception.py          # 时间/天气
│   ├── persona.py             # 人设 + 精力/作息
│   ├── extensions/            # 插件系统（Tool / Hook）
│   │   └── builtin/           # 内置插件（天气工具等）
│   ├── engine/                # 对话循环 / 后台潜意识 / 自我进化
│   └── channels/              # 渠道抽象（weixin / tui）
└── data/                      # 记忆库 + 微信凭据
```

## 扩展指南（插件系统）

新增能力 = 实现接口 + 注册，**不改核心代码**。

### 加一个工具（Tool）

```python
# src/extensions/builtin/my_tool.py
from src.extensions.base import Tool

class MyTool(Tool):
    name = "my_tool"
    description = "我的工具描述（给 LLM 看）"
    parameters = {"type": "object", "properties": {
        "arg": {"type": "string", "description": "参数"}
    }, "required": ["arg"]}

    async def run(self, arg: str = "") -> str:
        return f"结果: {arg}"

tool = MyTool()  # 顶层实例，自动注册
```

然后在 `main.py` 里 `plugins.load_module("src.extensions.builtin.my_tool")`。
Agent 对话时即可通过 function calling 自动调用它。

### 加一个钩子（Hook）

```python
from src.extensions.base import Hook

class MyHook(Hook):
    name = "my_hook"
    async def on_assistant_reply(self, user_id, text, reply):
        print("回复后触发:", reply)

hook = MyHook()
```

### 加一个渠道（Channel）

继承 `src.channels.base.Channel`，实现 `start(handler)` 和 `send(user_id, text)`，
在 `main.py` 的 `channels` 列表里加一个实例即可。

### 加一个模型（Model）

继承 `src.models.base.ModelClient`（或 OpenAI 兼容直接继承 `OpenAICompatModel`），
在 `main.py` 的 `ModelRouter` 里注册。

## 架构借鉴对照

| Pi 概念 | Friend Agent 实现 |
|---|---|
| `pi-ai` provider 抽象 / 跨 provider 切换 | `models/` + `router.py` |
| `pi-agent-core` agent-loop / 消息流 / hooks | `engine/agent_loop.py` + `context.py` |
| `pi-durable` Harness / compaction | `memory.py` |
| `pi-tui` 组件化 | `channels/tui.py`（textual） |

| Hermes 概念 | Friend Agent 实现 |
|---|---|
| 自我进化 / 持久记忆 | `engine/evolve.py` + `memory.py` |
| `SOUL.md` 灵魂文件 | `config/persona.json` |
| `gateway/platforms/weixin.py` | `channels/weixin.py`（iLink Bot API 移植） |
