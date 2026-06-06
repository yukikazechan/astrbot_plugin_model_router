# AstrBot Plugin Model Router

**基于意图识别的智能多模型路由插件 / Intelligent Multi-Model Router based on Intent Recognition**

`astrbot_plugin_model_router` 是一个为 AstrBot 设计的高级插件，它能够拦截用户消息，利用一个快速的 "路由模型" (Router Model) 对用户意图进行分析和打分，然后根据任务的**难度 (Difficulty)** 和 **类别 (Category)** 自动将请求分发给最合适的模型。

> Fork maintained by xkeyC(Fork): https://github.com/xkeyC/astrbot_plugin_model_router

实现 "简单问题用小模型（快/省），复杂问题用大模型（强/准）" 的最佳能效比。

## 📋 更新日志 (Changelog)

### v0.7.0
- ✨ **默认 Low Tier**: 插件启用后，非动态路由会话默认使用 Low Tier 的全局模型。
- ✨ **按群开启动态路由**: 只有 `routing_control.dynamic_group_ids` 中的群号或会话 ID 会执行意图分析和动态分发。
- ✨ **用户模型锁定**: 新增 `user_model_locks`，可将某个发送者锁定到指定 provider/model，优先级高于动态路由。
- 🔧 **适配当前 AstrBot Fork**: 路由入口改为 `on_waiting_llm_request`，并通过事件级 provider/model override 设置模型。
- 🔧 **修复上下文读取**: 从当前 AstrBot 的 `Conversation.history` JSON 中读取最近上下文。
- 🔧 **兼容旧配置**: 如果已有 `session_control.blacklist/whitelist`，会保留旧的跳过/启用动态路由语义。

### v0.6.0 (2025-12-15)
- ✨ **任务快照系统**: 支持多任务上下文追踪，AI 可在多个活跃任务间智能选择延续哪个
- ✨ **多维度难度评分**: 针对 code/math/roleplay/chat 等不同维度提供独立的评分标准
- ✨ **上下文关系判断**: 新增 `context_relation` (continue/downgrade/unrelated) 和 `continued_task_id` 字段
- ✨ **轮数过期机制**: 快照有效期与 `context_turns` 配置挂钩，而非固定时间
- ✨ **可配置上下文长度**: 新增 `context_max_chars` 配置，允许自定义每条消息的最大字符数
- 🐛 **修复延续判断**: 强化 Prompt 规则，确保 continue/downgrade 时必须提供有效的 task_id

## ✨ 主要功能 (Features)

*   **🧠 智能意图识别**: 自动分析用户输入是闲聊、代码、角色扮演、数学问题还是其他自定义类别。
*   **⚖️ 难度分级路由**: 
    *   **🟢 Low Tier (1-3分)**: 简单问候、天气查询 -> 路由到轻量模型。
    *   **🟡 Mid Tier (4-7分)**: 常规逻辑、文案生成 -> 路由到标准模型。
    *   **🔴 High Tier (7-9分)**: 复杂推理、深度编程 -> 路由到高性能模型。
*   **📝 上下文感知**: 路由判断时会参考最近的对话历史，避免因上下文缺失导致的误判。
*   **🛡️ 按群启用动态路由**: 默认使用 Low Tier 模型，仅对指定群或会话启用动态路由。
*   **🔒 用户模型锁定**: 支持将某个用户固定到指定 provider/model。
*   **⚙️ 高度可配置**: 支持自定义分类规则、难度阈值、提示词模板。
*   **🐛 详细调试模式**: 实时查看路由器的思考过程、评分和分发结果。

## 📦 安装 (Installation)

1.  将此插件文件夹 `astrbot_plugin_model_router` 放入 AstrBot 的 `data/plugins/` 目录中。
2.  重启 AstrBot。
3.  在 AstrBot 管理面板中配置插件，或使用通过配置文件进行设置。

## ⚙️ 配置说明 (Configuration)

配置分为 **核心设置 (Core)**、**等级框架 (Tiers)**、**路由控制 (Routing Control)** 和 **用户锁定 (User Locks)**。

### 1. 核心设置 (Router Config)

| 配置项 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `plugin_enabled` | bool | `true` | 插件总开关。 |
| `router_config.router_provider` | string | - | **(必须)** 负责分析意图的路由模型服务商。建议使用响应快、便宜的小模型。 |
| `router_config.router_model` | string | - | 指定路由使用的具体模型名称 (留空则用默认)。 |
| `router_config.debug_mode` | bool | `false` | 开启后，会在控制台或指定会话显示详细的路由分析日志。 |
| `router_config.context_turns` | int | `4` | 路由判断时参考的对话轮数 (1轮=一问一答)，同时控制任务快照的有效期。 |
| `router_config.context_max_chars` | int | `500` | 每条上下文消息的最大字符数。设置 0 表示不截断。 |

### 2. 等级框架 (Three Tiers)

插件将任务难度划分为三个等级，每个等级可以单独配置默认模型和特定分类的规则。

| 等级 | 适用场景 | 关键配置 |
| :--- | :--- | :--- |
| **🟢 Low Tier** | 闲聊、简单指令、单一工具调用 | **Max Score (默认3)**: 分数 <= 此值进入 Low Tier。<br>**Global Model**: 此等级的默认模型。<br>**Rules (r1-r6)**: 针对特定分类 (如 math, chat) 指定模型。 |
| **🟡 Mid Tier** | 一般逻辑、多步任务、文档摘要 | **Max Score (默认7)**: 分数 <= 此值 (且 > Low) 进入 Mid Tier。<br>**Global Model**: 此等级的默认模型。 |
| **🔴 High Tier** | 复杂代码、深度推理、创意写作 | **Global Model**: 此等级的默认模型 (通常配置为最强模型)。<br>**Rules (r1-r6)**: 同样可针对分类指定模型。 |

**规则 (Rules) 配置说明:**
每个 Tier 下可以配置 r1 到 r6 六组规则，每组规则包含：
*   `name`: 识别键名 (如 `code`)，对应路由模型分析出的 `category`。
*   `desc`: 意图定义 (如 `Programming tasks`)，会注入到路由模型的 Prompt 中。
*   `provider`: 命中该分类时使用的服务商。
*   `model`: 命中该分类时使用的模型名。

### 3. 路由控制 (Routing Control)

| 配置项 | 说明 |
| :--- | :--- |
| `routing_control.dynamic_group_ids` | 启用动态路由的群号或 `unified_msg_origin`。不在列表中的请求默认走 Low Tier 全局模型。 |

已有旧配置 `session_control.blacklist/whitelist` 时仍会被兼容读取：blacklist 命中的会话会跳过模型路由；whitelist 命中的会话会启用动态路由，未命中的会话会跳过模型路由。

### 4. 用户模型锁定 (User Locks)

`user_model_locks` 用于将某个发送者锁定到指定 provider/model。锁定规则优先级最高，命中后会跳过动态路由。

| 配置项 | 说明 |
| :--- | :--- |
| `sender_id` | 要锁定的用户 ID。 |
| `group_id` | 可选，留空表示所有群/私聊生效；填写群号或 `unified_msg_origin` 表示只在该范围生效。 |
| `provider_id` | 锁定使用的 provider。 |
| `model_name` | 锁定使用的模型名，留空则使用 provider 默认模型。 |

## 路由优先级

1. 用户模型锁定：命中 `user_model_locks` 后直接使用锁定模型。
2. 已存在的事件级模型选择：如果其他插件或平台已经设置 provider/model，模型路由器不覆盖。
3. 群动态路由：当前群/会话在 `dynamic_group_ids` 中时，调用 Router Model 分析并分发到 Low/Mid/High Tier。
4. 默认 Low Tier：其他请求直接使用 `tier_low.global_provider` / `tier_low.global_model`。

## 💻 指令 (Commands)

插件提供 `/router` 指令进行运行时管理：

| 指令 | 说明 | 示例 |
| :--- | :--- | :--- |
| `/router config` | 以表格形式显示当前的路由规则配置。 | `/router config` |
| `/router status` | 查看插件启用状态、当前路由模型等信息。 | `/router status` |
| `/router debug [on/off]` | 开启或关闭调试模式。不带参数则切换状态。 | `/router debug on` |
| `/router groups` | 显示已启用动态路由的群/会话。 | `/router groups` |
| `/router group add [group_id\|sid]` | 为当前群或指定群/会话开启动态路由。 | `/router group add` |
| `/router group remove [group_id\|sid]` | 关闭当前群或指定群/会话的动态路由。 | `/router group remove` |
| `/router locks` | 显示用户模型锁定规则。 | `/router locks` |
| `/router lock add <sender_id> <provider_id> [model] [group_id\|sid]` | 锁定用户模型。 | `/router lock add 123 provider_a gpt-4o-mini` |
| `/router lock remove <sender_id> [group_id\|sid]` | 删除用户模型锁定。 | `/router lock remove 123` |

---

## 🎨 技术实现 (Technical Implementation)

本插件深度集成 AstrBot 的 **Pipeline 架构** 和 **事件钩子系统**。下图和表格基于对 AstrBot 核心源码的分析。

### AstrBot Pipeline 阶段总览

AstrBot 使用洋葱模型处理消息，按顺序执行以下 9 个 Stage：

| # | Stage 名称 | 职责 |
|:---:|:---|:---|
| 1 | `WakingCheckStage` | 检查唤醒条件 (@ / wake_prefix)，**激活匹配的 Handler** |
| 2 | `WhitelistCheckStage` | 群聊/私聊白名单检查 |
| 3 | `SessionStatusCheckStage` | 会话启用状态检查 |
| 4 | `RateLimitStage` | 频率限制检查 |
| 5 | `ContentSafetyCheckStage` | 内容安全审核 |
| 6 | `PreProcessStage` | 预处理 |
| 7 | **`ProcessStage`** | **核心处理：调用插件 Handler 或 LLM** |
| 8 | `ResultDecorateStage` | 结果装饰 (T2I / 语音转换) |
| 9 | `RespondStage` | 发送消息 |

### 本插件的工作流程

1. AstrBot 确认消息会进入 LLM 流程后，触发 `OnWaitingLLMRequestEvent`。
2. 插件先检查 `user_model_locks`，命中则直接写入事件级 provider/model override。
3. 如果当前群或会话在 `routing_control.dynamic_group_ids` 中，插件调用 Router Model 做意图分析，再按 Low/Mid/High Tier 选择目标 provider/model。
4. 如果未启用动态路由或路由分析失败，插件默认写入 Low Tier 的全局 provider/model。
5. 后续 AstrBot 构建 LLM 请求时读取事件上的 `selected_provider` / `selected_model`，使用插件选择的模型。

### 关键代码对照表

| 阶段 | 插件代码 | AstrBot 核心代码 | 说明 |
|:---|:---|:---|:---|
| **Handler 注册** | `@filter.on_waiting_llm_request(priority=-100)` | `internal.py` L215 | 在 LLM 请求构建前执行，避免普通消息被插件误唤醒 |
| **注入决策** | `event.set_selected_provider(provider_id, model_name)` | `astr_message_event.py` | 将目标模型 ID 写入事件级 override |
| **读取决策** | - | `astr_main_agent.py` `_select_provider()` | 优先读取 `selected_provider`，否则使用默认 |
| **Debug 输出** | `@after_message_sent()` | `star_handler.py` L529-540 | 注册 `OnAfterMessageSentEvent` 类型的 Handler |

### 核心机制解析

1.  **后置 LLM 钩子**: 插件只在 AstrBot 已决定请求 LLM 后运行，不会让普通群消息被误唤醒。
2.  **事件级 override**: `event.set_selected_provider()` 只影响当前请求，不会修改会话配置。
3.  **优先级清晰**: 用户锁定 > 群动态路由 > 默认 Low Tier。

---

### Tips
*   `Router Model` 应选择响应速度快的模型，以减少路由判断带来的延迟。
*   **Debugging**: 如果发现路由不准确，开启 `/router debug on`，观察 `reasoning` 字段，根据模型的思考过程微调配置中的 `desc` (意图定义)。
