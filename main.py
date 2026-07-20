
import json
import time

from astrbot.api import AstrBotConfig
from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.event.filter import after_message_sent
from astrbot.core.provider.entities import ProviderRequest

from .routing import IntentRouter
from .umo_filter import get_active_umo_list, get_filter_mode, is_umo_allowed

@register(
    "astrbot_plugin_model_router",
    "Antigravity",
    "基于意图识别的多模型路由插件",
    "0.6.1",
    "https://github.com/yukikazechan/astrbot_plugin_model_router"
)
class ModelRouterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.router = IntentRouter(context, config)
        self.task_snapshots = {}  # {session_id: {task_id: {score, category, summary, time}}}

    def _save_config(self):
        """Persist runtime configuration changes when AstrBotConfig is available."""
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()

    def _get_session_config(self) -> dict:
        """Return a mutable session-control configuration object."""
        session_config = self.config.get("session_control")
        if not isinstance(session_config, dict):
            session_config = {}
            self.config["session_control"] = session_config
        return session_config

    @filter.event_message_type(filter.EventMessageType.ALL, priority=9999)
    async def pre_route_message(self, event: AstrMessageEvent):
        """
        High-priority message handler that runs BEFORE the LLM stage.
        Analyzes intent and sets selected_provider/selected_model on the event.
        This way, when InternalAgentSubStage._select_provider() runs, it will find our selection.
        """
        # Skip if not activated (wake word/@ check)
        if not event.is_at_or_wake_command:
            return
        
        # 0. Plugin Switch
        if not self.config.get("plugin_enabled", True):
            return
        
        # 0.5 Skip if a command handler is matched (e.g., /model, /help, etc.)
        # handlers_parsed_params contains only handlers that matched as commands
        handlers_parsed_params = event.get_extra("handlers_parsed_params", {})
        if handlers_parsed_params:
            logger.debug(f"🔌 Router: Skipping - command matched: {list(handlers_parsed_params.keys())}")
            return
        
        # 1. UMO Filtering (Blacklist/Whitelist)
        umo = event.unified_msg_origin
        session_cfg = self.config.get("session_control", {})
        if not is_umo_allowed(umo, session_cfg):
            filter_mode = get_filter_mode(session_cfg)
            logger.debug(
                f"🔌 Router: UMO {umo} rejected by {filter_mode}, skipping."
            )
            return
        
        # 2. Analyze Intent (using message_str since we don't have ProviderRequest yet)
        try:
            start_time = time.time()
            
            user_text = event.message_str
            if not user_text:
                return
            
            # Get recent context from conversation if available
            contexts = []
            try:
                conv_mgr = self.context.conversation_manager
                umo = event.unified_msg_origin
                cid = await conv_mgr.get_curr_conversation_id(umo)
                if cid:
                    conv = await conv_mgr.get_conversation(umo, cid)
                    if conv and conv.messages:
                        # Get max chars setting (0 = no truncation)
                        max_chars = self.config.get("router_config", {}).get("context_max_chars", 500)
                        # Get last few messages for context
                        for msg in conv.messages[-6:]:
                            content = str(msg.content)
                            if max_chars > 0:
                                content = content[:max_chars]
                            contexts.append({"role": msg.role, "content": content})
            except Exception as e:
                logger.debug(f"Could not get conversation context: {e}")
            
            # === 获取任务快照 ===
            sid = event.unified_msg_origin
            session_snapshots = self.task_snapshots.get(sid, {})
            
            # 获取配置的上下文轮数
            context_turns = self.config.get("router_config", {}).get("context_turns", 4)
            
            # 清理过期快照 (基于轮数，而非时间)
            # 每个快照有 turn_count，每次对话后递增
            # 当 turn_count 超过 context_turns 时过期
            valid_snapshots = {}
            for task_id, snap in session_snapshots.items():
                snap["turn_count"] = snap.get("turn_count", 0) + 1
                if snap["turn_count"] <= context_turns:
                    valid_snapshots[task_id] = snap
                else:
                    logger.debug(f"📤 Snapshot expired: {task_id} (turn {snap['turn_count']} > {context_turns})")
            self.task_snapshots[sid] = valid_snapshots
            
            # 构建快照列表供路由器使用
            snapshot_list = []
            for task_id, snap in valid_snapshots.items():
                snapshot_list.append({
                    "id": task_id,
                    "category": snap["category"],
                    "score": snap["score"],
                    "summary": snap.get("summary", "")[:100]
                })

            logger.info(f"🧩 Router analyzing: '{user_text[:30]}...' (Active snapshots: {len(snapshot_list)})")
            analysis = await self.router.analyze_intent(user_text, contexts, task_snapshots=snapshot_list)
            
            end_time = time.time()
            router_time_ms = (end_time - start_time) * 1000
            
            if not analysis:
                debug_on = self.config.get("router_config", {}).get("debug_mode", False)
                if debug_on:
                    logger.warning("⚠️ Router analysis returned None.")
                return
            
            ai_score = analysis.get("difficulty_score", 1)
            category = analysis.get("category", "chat")
            reasoning = analysis.get("reasoning", "")
            context_relation = analysis.get("context_relation", "unrelated")
            continued_task_id = analysis.get("continued_task_id")
            
            # === 根据 context_relation 决定最终分数 ===
            final_score = ai_score
            score_source = "ai"  # 用于 debug
            
            if context_relation == "continue" and continued_task_id:
                # 延续：使用快照分数
                continued_snap = valid_snapshots.get(continued_task_id)
                if continued_snap:
                    final_score = continued_snap["score"]
                    score_source = f"snapshot:{continued_task_id}"
                    logger.info(f"🔄 Context CONTINUE: Using snapshot score {final_score} from {continued_task_id}")
                    
            elif context_relation == "downgrade" and continued_task_id:
                # 降级：使用 AI 评判的分数
                score_source = f"downgrade:{continued_task_id}"
                logger.info(f"🔽 Context DOWNGRADE: AI re-evaluated to {final_score}")
                
            else:  # "unrelated" 或无有效快照
                score_source = "new"
                logger.info(f"🆕 Context UNRELATED: Independent score {final_score}")
            
            # === 更新快照 (仅当 score >= 4 且非纯闲聊) ===
            if final_score >= 4:
                # 生成新的 task_id
                task_id = f"task_{int(time.time()) % 10000}"
                # 从用户输入生成简短摘要
                summary = user_text[:50] + ("..." if len(user_text) > 50 else "")
                
                valid_snapshots[task_id] = {
                    "score": final_score,
                    "category": category,
                    "summary": summary,
                    "turn_count": 0  # 新快照从 0 开始计数
                }
                self.task_snapshots[sid] = valid_snapshots
                logger.debug(f"📸 Snapshot saved: {task_id} (Score {final_score}, Cat: {category})")
            # 注意：低分闲聊不更新快照，保留之前的高难度任务记录
            
            # 3. Get Target Provider/Model
            t_provider_id, t_model_name, t_tier_name = self.get_target_config(category, final_score)
            
            debug_on = self.config.get("router_config", {}).get("debug_mode", False)
            if debug_on:
                logger.info(f"🎯 Routing: {category} (Score {final_score} | {t_tier_name}) -> {t_provider_id}:{t_model_name}")
            
            if not t_provider_id:
                # No routing configured, let AstrBot use default
                return
            
            # 4. Validate target provider exists
            t_provider = self.context.get_provider_by_id(t_provider_id)
            if not t_provider:
                logger.error(f"❌ Target provider '{t_provider_id}' not found.")
                return
            
            logger.info(f"✅ Router: Pre-selecting provider={t_provider_id}, model={t_model_name or 'default'}")
            
            # 5. Set the selected provider and model on the event
            # This will be picked up by InternalAgentSubStage._select_provider()
            event.set_extra("selected_provider", t_provider_id)
            if t_model_name:
                event.set_extra("selected_model", t_model_name)
            
            # Store debug data for later (will be formatted and sent in on_after_message_sent)
            if debug_on:
                event.set_extra("_router_debug_data", {
                    "time_ms": router_time_ms,
                    "router_model": self.config.get("router_config", {}).get("router_model", "Default"),
                    "category": category,
                    "ai_score": ai_score,
                    "final_score": final_score,
                    "tier_name": t_tier_name,
                    "model_display": t_model_name or 'Default',
                    "context_relation": context_relation,
                    "continued_task_id": continued_task_id,
                    "score_source": score_source,
                    "active_snapshots": len(valid_snapshots),
                    "reasoning": reasoning,
                    "origin_sid": event.unified_msg_origin
                })
            
            # Don't stop event - let AstrBot continue with our selected provider
            
        except Exception as e:
            logger.error(f"Router error in pre_route_message: {e}")
            import traceback
            logger.error(traceback.format_exc())

    @after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        """消息发送后，创建并发送 debug 信息到指定 SID"""
        debug_data = event.get_extra("_router_debug_data")
        if not debug_data:
            return
        
        # 清除标记，避免重复发送
        event.set_extra("_router_debug_data", None)
        
        # 检查是否是原始请求的会话
        if debug_data.get("origin_sid") != event.unified_msg_origin:
            return
        
        # 获取配置的 debug 目标 SID
        debug_target_sid = self.config.get("router_config", {}).get("debug_target_sid", "")
        if not debug_target_sid:
            # 没有配置目标 SID，只记录到日志
            logger.info(f"[Router Debug] {debug_data}")
            return
        
        # 格式化 debug 消息 (新增 context_relation 等字段)
        context_info = f"📋 Context: {debug_data['context_relation']}"
        if debug_data['continued_task_id']:
            context_info += f" -> {debug_data['continued_task_id']}"
        
        score_info = f"Score {debug_data['final_score']}"
        if debug_data['ai_score'] != debug_data['final_score']:
            score_info = f"Score {debug_data['ai_score']}→{debug_data['final_score']}"
        
        debug_msg = (
            f"[🧩 Model Router Debug]\n"
            f"⏱️ Time: {debug_data['time_ms']:.1f}ms\n"
            f"🤖 Router: {debug_data['router_model']}\n"
            f"{context_info} (Snapshots: {debug_data['active_snapshots']})\n"
            f"🎯 Target: {debug_data['category']} ({score_info} | {debug_data['tier_name']}) -> {debug_data['model_display']}\n"
            f"💡 Reasoning: {debug_data['reasoning']}"
        )
        
        # 发送到指定 SID
        try:
            from astrbot.core.message.components import Plain
            from astrbot.core.message.message_event_result import MessageChain
            
            message_chain = MessageChain()
            message_chain.chain.append(Plain(debug_msg))
            
            success = await self.context.send_message(debug_target_sid, message_chain)
            if not success:
                logger.warning(f"Failed to find platform for debug SID: {debug_target_sid}")
        except Exception as e:
            logger.error(f"Failed to send debug to {debug_target_sid}: {e}")

        
    def get_target_config(self, category: str, difficulty: int):
        """Get target provider and model based on category and difficulty."""
        # Determine tier based on difficulty
        if difficulty <= 3:
            curr_tier = "low"
        elif difficulty <= 6:
            curr_tier = "mid"
        else:
            curr_tier = "high"
        
        tier_key = f"tier_{curr_tier}"
        tier_config = self.config.get(tier_key, {})
        
        # First try to find a category-specific routing
        targeted_provider = None
        targeted_model = None
        
        for i in range(1, 7):  # r1 to r6
            name = tier_config.get(f"r{i}_name", "")
            if name and name.lower() == category.lower():
                targeted_provider = tier_config.get(f"r{i}_provider", "")
                targeted_model = tier_config.get(f"r{i}_model", "")
                break
        
        # Fall back to global provider/model for tier if no specific routing found
        if not targeted_provider:
            targeted_provider = tier_config.get("global_provider", "")
            targeted_model = tier_config.get("global_model", "")
            
        return targeted_provider, targeted_model, curr_tier

    def get_fallback_config(self, tier_name: str):
        """Get fallback (global) provider and model for a tier."""
        tier_key = f"tier_{tier_name}"
        tier_config = self.config.get(tier_key, {})
        return tier_config.get("global_provider"), tier_config.get("global_model", "")


    def _generate_config_table(self) -> str:
        """Generate a vertical-style model configuration display."""
        # 收集所有 category
        categories = set()
        for tier in ["tier_low", "tier_mid", "tier_high"]:
            tier_cfg = self.config.get(tier, {})
            for i in range(1, 7):
                name = tier_cfg.get(f"r{i}_name", "")
                if name:
                    categories.add(name)
        
        if not categories:
            return "No routing rules configured."
        
        lines = []
        
        # 按 category 分组显示
        for category in sorted(categories):
            lines.append(f"\n▸ {category}")
            
            # 获取每个 tier 的配置
            tier_icons = {"tier_low": "🟢Low", "tier_mid": "🟡Mid", "tier_high": "🔴High"}
            
            for tier_key, tier_label in tier_icons.items():
                tier_cfg = self.config.get(tier_key, {})
                model = ""
                is_global = False
                
                # 查找 category 专属配置
                for i in range(1, 7):
                    if tier_cfg.get(f"r{i}_name", "").lower() == category.lower():
                        model = tier_cfg.get(f"r{i}_model", "")
                        break
                
                # 如果没有专属配置，使用 Global
                if not model:
                    model = tier_cfg.get("global_model", "")
                    is_global = True
                
                display = "Global" if is_global else (model or "-")
                lines.append(f"  {tier_label}: {display}")
        
        return "\n".join(lines)

    @filter.command("router")
    async def router_command(self, event: AstrMessageEvent):
        """模型路由器管理命令"""
        args = event.message_str.split()
        
        if len(args) < 2:
            result = event.plain_result(
                "🧩 模型路由器命令:\n"
                "/router config - 显示当前路由配置\n"
                "/router debug - 切换调试模式\n"
                "/router status - 查看路由器状态\n"
                "/router mode [blacklist|whitelist] - 切换 UMO 过滤模式\n"
                "/router list - 显示当前 UMO 名单\n"
                "/router add [umo] - 添加 UMO 到当前名单\n"
                "/router remove [umo] - 从当前名单移除 UMO"
            )
            result.use_t2i(False)
            return result
        
        sub_cmd = args[1].lower()
        
        if sub_cmd == "config":
            table = self._generate_config_table()
            result = event.plain_result(f"📊 模型路由配置:\n{table}")
            result.use_t2i(False)  # 禁止文转图
            return result
        
        elif sub_cmd == "debug":
            if len(args) < 3:
                # 没有参数时，切换当前状态
                if "router_config" not in self.config:
                    self.config["router_config"] = {}
                current = self.config["router_config"].get("debug_mode", False)
                new_state = not current
                self.config["router_config"]["debug_mode"] = new_state
                return event.plain_result(f"🐛 Debug mode: {'ON' if new_state else 'OFF'}")
            
            mode = args[2].lower()
            if mode in ["on", "true", "1"]:
                if "router_config" not in self.config:
                    self.config["router_config"] = {}
                self.config["router_config"]["debug_mode"] = True
                return event.plain_result("🐛 Debug mode enabled")
            elif mode in ["off", "false", "0"]:
                if "router_config" not in self.config:
                    self.config["router_config"] = {}
                self.config["router_config"]["debug_mode"] = False
                return event.plain_result("🐛 Debug mode disabled")
            else:
                return event.plain_result("Usage: /router debug [on|off]")
        
        elif sub_cmd == "status":
            enabled = self.config.get("plugin_enabled", True)
            debug = self.config.get("router_config", {}).get("debug_mode", False)
            router_provider = self.config.get("router_config", {}).get("router_provider", "Not set")
            router_model = self.config.get("router_config", {}).get("router_model", "Not set")
            
            return event.plain_result(
                f"🧩 Model Router Status:\n"
                f"- Enabled: {'Yes' if enabled else 'No'}\n"
                f"- Debug: {'On' if debug else 'Off'}\n"
                f"- Router LLM: {router_provider}:{router_model}\n"
                f"- Version: 0.6.1"
            )
        
        elif sub_cmd == "mode":
            if len(args) < 3 or args[2].lower() not in ("blacklist", "whitelist"):
                return event.plain_result(
                    "Usage: /router mode [blacklist|whitelist]"
                )

            filter_type = args[2].lower()
            session_cfg = self._get_session_config()
            session_cfg["filter_type"] = filter_type
            self._save_config()
            return event.plain_result(f"✅ UMO filter mode: {filter_type.upper()}")

        elif sub_cmd == "list":
            session_cfg = self.config.get("session_control", {})
            filter_type = get_filter_mode(session_cfg)
            active_umos = get_active_umo_list(session_cfg)

            lines = [f"📋 UMO Filter Mode: {filter_type.upper()}"]
            icon = "✅" if filter_type == "whitelist" else "🚫"
            lines.append(
                f"\n{icon} {filter_type.title()} ({len(active_umos)} entries):"
            )
            if active_umos:
                for umo in active_umos[:20]:  # 最多显示20个
                    lines.append(f"  • {umo}")
                if len(active_umos) > 20:
                    lines.append(f"  ... and {len(active_umos) - 20} more")
            else:
                lines.append("  (empty)")
            
            result = event.plain_result("\n".join(lines))
            result.use_t2i(False)
            return result
        
        elif sub_cmd == "add":
            if len(args) < 3:
                # 使用当前会话的 UMO
                umo = event.unified_msg_origin
            else:
                umo = args[2].strip()
            
            session_cfg = self._get_session_config()
            list_key = get_filter_mode(session_cfg)
            active_umos = get_active_umo_list(session_cfg)

            if umo and umo not in active_umos:
                active_umos.append(umo)
                session_cfg[list_key] = active_umos
                self._save_config()
                return event.plain_result(f"✅ Added to {list_key}: {umo}")
            else:
                return event.plain_result(f"⚠️ Already in {list_key}: {umo}")
        
        elif sub_cmd == "remove":
            if len(args) < 3:
                umo = event.unified_msg_origin
            else:
                umo = args[2].strip()
            
            session_cfg = self._get_session_config()
            list_key = get_filter_mode(session_cfg)
            active_umos = get_active_umo_list(session_cfg)

            if umo in active_umos:
                active_umos.remove(umo)
                session_cfg[list_key] = active_umos
                self._save_config()
                return event.plain_result(f"✅ Removed from {list_key}: {umo}")
            else:
                return event.plain_result(f"⚠️ Not found in {list_key}: {umo}")
        
        else:
            return event.plain_result(f"Unknown subcommand: {sub_cmd}\nUse /router for help.")
