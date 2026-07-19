# AstrBot Plugin Model Router

一个简单、确定性的 AstrBot 模型路由插件。它不再调用额外的“路由模型”，也没有 Low/Mid/High 多档评分；每次请求只在本地按配置顺序检查规则。

## 路由方式

- **关键字路由**：消息包含任一或全部关键字时，使用指定 provider/model。
- **正则路由**：消息被 Python 正则表达式 `re.search` 命中时，使用指定 provider/model。
- **指定用户路由**：发送者 ID 相符时，使用指定 provider/model。
- **默认模型**：所有规则都未命中时使用；留空则保持 AstrBot 原本选择的模型。

每条规则都可以填写可选的“群号或会话 ID”，把它限制在某个群或 `unified_msg_origin`。规则从上到下匹配，第一条命中后停止，因此更具体的规则应放在更前面。

## 配置示例

在 AstrBot 插件配置页的 `routing_rules` 中添加规则即可。例如：

1. 添加“指定用户路由”，`sender_id = 10001`，目标为专用模型。
2. 添加“关键字路由”，关键字为 `代码`、`Python`，`match_mode = any`，目标为代码模型。
3. 添加“正则路由”，表达式为 `(?i)\b(translate|翻译)\b`，目标为翻译模型。
4. 配置 `default_provider_id` 和 `default_model_name` 作为兜底。

目标模型名称留空时，使用所选 provider 的默认模型。无效正则不会中断消息处理，插件会记录警告并继续检查后面的规则。

## 路由优先级

1. 如果其他组件已经为当前事件选择了 provider/model，本插件不覆盖。
2. `routing_rules` 从上到下第一条命中的规则。
3. `default_provider_id` / `default_model_name`。
4. 默认 provider 也留空时，保持 AstrBot 原选择。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/router status` | 查看启用状态、规则数量和默认模型。 |
| `/router rules` | 查看当前规则及顺序。 |
| `/router test <文本>` | 使用当前发送者和会话测试文本会命中哪条规则。 |

## 从 v0.7 升级

v1.0 移除了路由 LLM、难度评分、三档模型、分类映射、动态群列表和任务快照。旧配置不会再执行 AI 动态路由。

这是一次配置结构升级。更新后请在管理面板重新填写 `routing_rules`、`default_provider_id` 和 `default_model_name`；旧的 Tier 与用户锁定字段会由 AstrBot 配置完整性检查移除。

## 安装

将本目录放入 AstrBot 的 `data/plugins/`，重启 AstrBot，然后在管理面板中配置插件。

## 实现说明

插件在 `on_waiting_llm_request` 阶段匹配规则，并通过 `event.set_selected_provider()` 为当前请求设置事件级 provider/model，不修改会话的永久模型配置。
