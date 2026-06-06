import json
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from .routing import IntentRouter

PLUGIN_VERSION = "0.7.0"


@register(
    "astrbot_plugin_model_router",
    "xkeyC(Fork)",
    "基于意图识别的多模型路由插件",
    PLUGIN_VERSION,
    "https://github.com/xkeyC/astrbot_plugin_model_router",
)
class ModelRouterPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.router = IntentRouter(context, config)
        self.task_snapshots: dict[str, dict[str, dict[str, Any]]] = {}

    @filter.on_waiting_llm_request(priority=-100)
    async def route_llm_request(self, event: AstrMessageEvent):
        """Select the provider/model immediately before AstrBot builds the LLM request."""
        if not self.config.get("plugin_enabled", True):
            return
        if not event.is_at_or_wake_command:
            return

        locked = self._get_locked_user_model(event)
        if locked:
            provider_id, model_name, rule_name = locked
            if self._apply_provider_selection(event, provider_id, model_name):
                logger.info(
                    "[ModelRouter] user lock matched rule=%s provider=%s model=%s",
                    rule_name or "unnamed",
                    provider_id,
                    model_name or "default",
                )
            return

        if self._has_existing_model_selection(event):
            logger.debug(
                "[ModelRouter] skip because provider/model is already selected"
            )
            return

        low_provider_id, low_model_name = self.get_fallback_config("low")
        legacy_decision = self._get_legacy_session_control_decision(event)
        if legacy_decision == "skip":
            logger.debug("[ModelRouter] skipped by legacy session_control")
            return

        dynamic_routing_enabled = legacy_decision == "dynamic" or (
            legacy_decision is None and self._is_dynamic_routing_enabled(event)
        )
        if not dynamic_routing_enabled:
            self._apply_default_low_model(event, low_provider_id, low_model_name)
            return

        await self._route_dynamic(event, low_provider_id, low_model_name)

    async def _route_dynamic(
        self,
        event: AstrMessageEvent,
        low_provider_id: str,
        low_model_name: str,
    ) -> None:
        start_time = time.time()
        user_text = event.message_str
        if not user_text:
            self._apply_default_low_model(event, low_provider_id, low_model_name)
            return

        try:
            contexts = await self._get_recent_contexts(event)
            snapshot_list = self._refresh_task_snapshots(event)

            logger.info(
                "[ModelRouter] analyzing '%s...' snapshots=%s",
                user_text[:30],
                len(snapshot_list),
            )
            analysis = await self.router.analyze_intent(
                user_text,
                contexts,
                task_snapshots=snapshot_list,
            )
            router_time_ms = (time.time() - start_time) * 1000
            if not analysis:
                self._apply_default_low_model(event, low_provider_id, low_model_name)
                return

            ai_score = int(analysis.get("difficulty_score", 1) or 1)
            category = str(analysis.get("category", "chat") or "chat")
            reasoning = str(analysis.get("reasoning", "") or "")
            context_relation = str(
                analysis.get("context_relation", "unrelated") or "unrelated",
            )
            continued_task_id = analysis.get("continued_task_id")

            final_score, score_source = self._resolve_context_score(
                event,
                ai_score,
                context_relation,
                continued_task_id,
            )
            self._save_task_snapshot(event, final_score, category, user_text)

            provider_id, model_name, tier_name = self.get_target_config(
                category,
                final_score,
            )
            if not provider_id:
                provider_id = low_provider_id
                model_name = low_model_name
                tier_name = "low"

            if self._apply_provider_selection(event, provider_id, model_name):
                logger.info(
                    "[ModelRouter] routed category=%s score=%s tier=%s provider=%s model=%s",
                    category,
                    final_score,
                    tier_name,
                    provider_id,
                    model_name or "default",
                )
                self._set_debug_data(
                    event=event,
                    router_time_ms=router_time_ms,
                    category=category,
                    ai_score=ai_score,
                    final_score=final_score,
                    tier_name=tier_name,
                    model_name=model_name,
                    context_relation=context_relation,
                    continued_task_id=continued_task_id,
                    score_source=score_source,
                    reasoning=reasoning,
                )
        except Exception as exc:
            logger.error("[ModelRouter] dynamic route failed: %s", exc, exc_info=True)
            self._apply_default_low_model(event, low_provider_id, low_model_name)

    def _apply_default_low_model(
        self,
        event: AstrMessageEvent,
        provider_id: str,
        model_name: str,
    ) -> None:
        if not provider_id:
            return
        if self._apply_provider_selection(event, provider_id, model_name):
            logger.info(
                "[ModelRouter] using default low-tier provider=%s model=%s",
                provider_id,
                model_name or "default",
            )

    def _apply_provider_selection(
        self,
        event: AstrMessageEvent,
        provider_id: str,
        model_name: str = "",
    ) -> bool:
        provider_id = str(provider_id or "").strip()
        model_name = str(model_name or "").strip()
        if not provider_id:
            return False
        if not self.context.get_provider_by_id(provider_id):
            logger.error("[ModelRouter] target provider not found: %s", provider_id)
            return False
        event.set_selected_provider(provider_id, model_name or None)
        return True

    def _has_existing_model_selection(self, event: AstrMessageEvent) -> bool:
        for getter_name in ("get_selected_provider", "get_selected_model"):
            getter = getattr(event, getter_name, None)
            if callable(getter):
                try:
                    if getter():
                        return True
                except Exception:
                    pass
        return bool(
            event.get_extra("selected_provider") or event.get_extra("selected_model")
        )

    def _get_legacy_session_control_decision(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        session_control = self.config.get("session_control")
        if not isinstance(session_control, dict):
            return None

        identifiers = self._get_event_identifiers(event)
        filter_type = str(session_control.get("filter_type", "blacklist")).strip()
        if filter_type == "whitelist":
            whitelist = set(self._get_string_list(session_control.get("whitelist", [])))
            return "dynamic" if identifiers & whitelist else "skip"

        blacklist = set(self._get_string_list(session_control.get("blacklist", [])))
        return "skip" if identifiers & blacklist else None

    async def _get_recent_contexts(
        self,
        event: AstrMessageEvent,
    ) -> list[dict[str, str]]:
        contexts: list[dict[str, str]] = []
        conv_mgr = self.context.conversation_manager
        cid = await conv_mgr.get_curr_conversation_id(event.unified_msg_origin)
        if not cid:
            return contexts
        conv = await conv_mgr.get_conversation(event.unified_msg_origin, cid)
        if not conv or not conv.history:
            return contexts

        max_chars = int(
            self.config.get("router_config", {}).get("context_max_chars", 500)
        )
        try:
            history = json.loads(conv.history)
        except json.JSONDecodeError:
            logger.debug("[ModelRouter] failed to parse conversation history")
            return contexts

        for item in history[-6:]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", ""))
            if max_chars > 0:
                content = content[:max_chars]
            if content:
                contexts.append(
                    {
                        "role": str(item.get("role", "user")),
                        "content": content,
                    }
                )
        return contexts

    def _refresh_task_snapshots(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        sid = event.unified_msg_origin
        session_snapshots = self.task_snapshots.get(sid, {})
        context_turns = int(
            self.config.get("router_config", {}).get("context_turns", 4)
        )

        valid_snapshots: dict[str, dict[str, Any]] = {}
        for task_id, snap in session_snapshots.items():
            snap["turn_count"] = int(snap.get("turn_count", 0)) + 1
            if snap["turn_count"] <= context_turns:
                valid_snapshots[task_id] = snap
            else:
                logger.debug(
                    "[ModelRouter] snapshot expired: %s turn=%s",
                    task_id,
                    snap["turn_count"],
                )
        self.task_snapshots[sid] = valid_snapshots
        return [
            {
                "id": task_id,
                "category": snap["category"],
                "score": snap["score"],
                "summary": str(snap.get("summary", ""))[:100],
            }
            for task_id, snap in valid_snapshots.items()
        ]

    def _resolve_context_score(
        self,
        event: AstrMessageEvent,
        ai_score: int,
        context_relation: str,
        continued_task_id: object,
    ) -> tuple[int, str]:
        if context_relation != "continue" or not continued_task_id:
            if context_relation == "downgrade" and continued_task_id:
                return ai_score, f"downgrade:{continued_task_id}"
            return ai_score, "new"

        snap = self.task_snapshots.get(event.unified_msg_origin, {}).get(
            str(continued_task_id)
        )
        if not snap:
            return ai_score, "new"
        return int(snap["score"]), f"snapshot:{continued_task_id}"

    def _save_task_snapshot(
        self,
        event: AstrMessageEvent,
        final_score: int,
        category: str,
        user_text: str,
    ) -> None:
        if final_score < 4:
            return
        sid = event.unified_msg_origin
        snapshots = self.task_snapshots.setdefault(sid, {})
        task_id = f"task_{int(time.time()) % 10000}"
        snapshots[task_id] = {
            "score": final_score,
            "category": category,
            "summary": user_text[:50] + ("..." if len(user_text) > 50 else ""),
            "turn_count": 0,
        }

    def _set_debug_data(
        self,
        *,
        event: AstrMessageEvent,
        router_time_ms: float,
        category: str,
        ai_score: int,
        final_score: int,
        tier_name: str,
        model_name: str,
        context_relation: str,
        continued_task_id: object,
        score_source: str,
        reasoning: str,
    ) -> None:
        if not self.config.get("router_config", {}).get("debug_mode", False):
            return
        event.set_extra(
            "_router_debug_data",
            {
                "time_ms": router_time_ms,
                "router_model": self.config.get("router_config", {}).get(
                    "router_model", "Default"
                ),
                "category": category,
                "ai_score": ai_score,
                "final_score": final_score,
                "tier_name": tier_name,
                "model_display": model_name or "Default",
                "context_relation": context_relation,
                "continued_task_id": continued_task_id,
                "score_source": score_source,
                "active_snapshots": len(
                    self.task_snapshots.get(event.unified_msg_origin, {})
                ),
                "reasoning": reasoning,
                "origin_sid": event.unified_msg_origin,
            },
        )

    def _is_dynamic_routing_enabled(self, event: AstrMessageEvent) -> bool:
        groups = self._get_string_list(
            self.config.get("routing_control", {}).get("dynamic_group_ids", [])
        )
        if not groups:
            return False
        identifiers = self._get_event_identifiers(event)
        return any(group in identifiers for group in groups)

    def _get_locked_user_model(
        self,
        event: AstrMessageEvent,
    ) -> tuple[str, str, str] | None:
        sender_id = event.get_sender_id()
        if not sender_id:
            return None
        identifiers = self._get_event_identifiers(event)
        for item in self.config.get("user_model_locks", []):
            if not isinstance(item, dict) or not item.get("rule_enabled", True):
                continue
            if str(item.get("sender_id", "")).strip() != sender_id:
                continue
            scope = str(item.get("group_id", "")).strip()
            if scope and scope not in identifiers:
                continue
            provider_id = str(item.get("provider_id", "")).strip()
            if not provider_id:
                continue
            return (
                provider_id,
                str(item.get("model_name", "")).strip(),
                str(item.get("rule_name", "")).strip(),
            )
        return None

    def _get_event_identifiers(self, event: AstrMessageEvent) -> set[str]:
        identifiers = {event.unified_msg_origin}
        if group_id := event.get_group_id():
            identifiers.add(str(group_id))
        if sender_id := event.get_sender_id():
            identifiers.add(f"p_{sender_id}")
        return identifiers

    def _get_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _save_config(self) -> None:
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        debug_data = event.get_extra("_router_debug_data")
        if not debug_data:
            return
        event.set_extra("_router_debug_data", None)
        if debug_data.get("origin_sid") != event.unified_msg_origin:
            return

        debug_target_sid = self.config.get("router_config", {}).get(
            "debug_target_sid", ""
        )
        if not debug_target_sid:
            logger.info("[ModelRouter Debug] %s", debug_data)
            return

        context_info = f"Context: {debug_data['context_relation']}"
        if debug_data["continued_task_id"]:
            context_info += f" -> {debug_data['continued_task_id']}"
        score_info = f"Score {debug_data['final_score']}"
        if debug_data["ai_score"] != debug_data["final_score"]:
            score_info = f"Score {debug_data['ai_score']}->{debug_data['final_score']}"
        debug_msg = (
            "[Model Router Debug]\n"
            f"Time: {debug_data['time_ms']:.1f}ms\n"
            f"Router: {debug_data['router_model']}\n"
            f"{context_info} (Snapshots: {debug_data['active_snapshots']})\n"
            f"Target: {debug_data['category']} ({score_info} | {debug_data['tier_name']}) "
            f"-> {debug_data['model_display']}\n"
            f"Reasoning: {debug_data['reasoning']}"
        )

        try:
            message_chain = MessageChain()
            message_chain.chain.append(Plain(debug_msg))
            success = await self.context.send_message(debug_target_sid, message_chain)
            if not success:
                logger.warning(
                    "[ModelRouter] failed to find platform for debug SID: %s",
                    debug_target_sid,
                )
        except Exception as exc:
            logger.error(
                "[ModelRouter] failed to send debug to %s: %s",
                debug_target_sid,
                exc,
            )

    def get_target_config(self, category: str, difficulty: int):
        if difficulty <= int(self.config.get("tier_low", {}).get("max_score", 3)):
            curr_tier = "low"
        elif difficulty <= int(self.config.get("tier_mid", {}).get("max_score", 7)):
            curr_tier = "mid"
        else:
            curr_tier = "high"

        tier_config = self.config.get(f"tier_{curr_tier}", {})
        targeted_provider = ""
        targeted_model = ""
        for i in range(1, 7):
            name = tier_config.get(f"r{i}_name", "")
            if name and name.lower() == category.lower():
                targeted_provider = tier_config.get(f"r{i}_provider", "")
                targeted_model = tier_config.get(f"r{i}_model", "")
                break

        if not targeted_provider:
            targeted_provider = tier_config.get("global_provider", "")
            targeted_model = tier_config.get("global_model", "")
        return targeted_provider, targeted_model, curr_tier

    def get_fallback_config(self, tier_name: str):
        tier_config = self.config.get(f"tier_{tier_name}", {})
        return tier_config.get("global_provider", ""), tier_config.get(
            "global_model", ""
        )

    def _generate_config_table(self) -> str:
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
        for category in sorted(categories):
            lines.append(f"\n▸ {category}")
            tier_icons = {"tier_low": "Low", "tier_mid": "Mid", "tier_high": "High"}
            for tier_key, tier_label in tier_icons.items():
                tier_cfg = self.config.get(tier_key, {})
                model = ""
                is_global = False
                for i in range(1, 7):
                    if tier_cfg.get(f"r{i}_name", "").lower() == category.lower():
                        model = tier_cfg.get(f"r{i}_model", "")
                        break
                if not model:
                    model = tier_cfg.get("global_model", "")
                    is_global = True
                display = "Global" if is_global else (model or "-")
                lines.append(f"  {tier_label}: {display}")
        return "\n".join(lines)

    @filter.command("router")
    async def router_command(self, event: AstrMessageEvent):
        args = event.message_str.split()
        if len(args) < 2:
            result = event.plain_result(
                "模型路由器命令:\n"
                "/router config - 显示当前路由配置\n"
                "/router status - 查看路由器状态\n"
                "/router debug [on|off] - 开关调试模式\n"
                "/router groups - 显示动态路由群列表\n"
                "/router group add [group_id|sid] - 为群开启动态路由\n"
                "/router group remove [group_id|sid] - 为群关闭动态路由\n"
                "/router locks - 显示用户模型锁定\n"
                "/router lock add <sender_id> <provider_id> [model] [group_id|sid] - 锁定用户模型\n"
                "/router lock remove <sender_id> [group_id|sid] - 删除用户模型锁定"
            )
            result.use_t2i(False)
            return result

        sub_cmd = args[1].lower()
        if sub_cmd == "config":
            result = event.plain_result(
                f"模型路由配置:\n{self._generate_config_table()}"
            )
            result.use_t2i(False)
            return result
        if sub_cmd == "status":
            return self._status_result(event)
        if sub_cmd == "debug":
            return self._debug_result(event, args)
        if sub_cmd in {"groups", "list"}:
            return self._groups_result(event)
        if sub_cmd == "group":
            return self._group_command(event, args)
        if sub_cmd in {"locks", "locklist"}:
            return self._locks_result(event)
        if sub_cmd == "lock":
            return self._lock_command(event, args)
        return event.plain_result(
            f"Unknown subcommand: {sub_cmd}\nUse /router for help."
        )

    def _status_result(self, event: AstrMessageEvent):
        enabled = self.config.get("plugin_enabled", True)
        debug = self.config.get("router_config", {}).get("debug_mode", False)
        router_provider = self.config.get("router_config", {}).get(
            "router_provider", "Not set"
        )
        router_model = self.config.get("router_config", {}).get(
            "router_model", "Not set"
        )
        low_provider, low_model = self.get_fallback_config("low")
        dynamic_groups = self._get_string_list(
            self.config.get("routing_control", {}).get("dynamic_group_ids", [])
        )
        return event.plain_result(
            "Model Router Status:\n"
            f"- Enabled: {'Yes' if enabled else 'No'}\n"
            f"- Debug: {'On' if debug else 'Off'}\n"
            f"- Router LLM: {router_provider}:{router_model}\n"
            f"- Default Low: {low_provider or 'Not set'}:{low_model or 'default'}\n"
            f"- Dynamic Groups: {len(dynamic_groups)}\n"
            f"- User Locks: {len(self.config.get('user_model_locks', []))}\n"
            f"- Version: {PLUGIN_VERSION}"
        )

    def _debug_result(self, event: AstrMessageEvent, args: list[str]):
        router_config = self.config.setdefault("router_config", {})
        if len(args) < 3:
            router_config["debug_mode"] = not router_config.get("debug_mode", False)
        else:
            mode = args[2].lower()
            if mode not in {"on", "off", "true", "false", "1", "0"}:
                return event.plain_result("Usage: /router debug [on|off]")
            router_config["debug_mode"] = mode in {"on", "true", "1"}
        self._save_config()
        return event.plain_result(
            f"Debug mode: {'ON' if router_config['debug_mode'] else 'OFF'}"
        )

    def _groups_result(self, event: AstrMessageEvent):
        groups = self._get_string_list(
            self.config.get("routing_control", {}).get("dynamic_group_ids", [])
        )
        lines = [f"Dynamic routing groups ({len(groups)}):"]
        lines.extend(f"- {group}" for group in groups[:50])
        if len(groups) > 50:
            lines.append(f"... and {len(groups) - 50} more")
        return event.plain_result("\n".join(lines))

    def _group_command(self, event: AstrMessageEvent, args: list[str]):
        if len(args) < 3 or args[2].lower() not in {"add", "remove"}:
            return event.plain_result(
                "Usage: /router group <add|remove> [group_id|sid]"
            )
        target = (
            args[3]
            if len(args) >= 4
            else (event.get_group_id() or event.unified_msg_origin)
        )
        target = str(target).strip()
        routing_control = self.config.setdefault("routing_control", {})
        groups = self._get_string_list(routing_control.get("dynamic_group_ids", []))
        if args[2].lower() == "add":
            if target not in groups:
                groups.append(target)
            action = "enabled"
        else:
            groups = [group for group in groups if group != target]
            action = "disabled"
        routing_control["dynamic_group_ids"] = groups
        self._save_config()
        return event.plain_result(f"Dynamic routing {action} for: {target}")

    def _locks_result(self, event: AstrMessageEvent):
        locks = [
            item
            for item in self.config.get("user_model_locks", [])
            if isinstance(item, dict) and item.get("rule_enabled", True)
        ]
        lines = [f"User model locks ({len(locks)}):"]
        for item in locks[:50]:
            scope = item.get("group_id") or "all"
            lines.append(
                f"- {item.get('sender_id')} @ {scope} -> "
                f"{item.get('provider_id')}:{item.get('model_name') or 'default'}"
            )
        if len(locks) > 50:
            lines.append(f"... and {len(locks) - 50} more")
        return event.plain_result("\n".join(lines))

    def _lock_command(self, event: AstrMessageEvent, args: list[str]):
        if len(args) < 3 or args[2].lower() not in {"add", "remove"}:
            return event.plain_result(
                "Usage: /router lock <add|remove> <sender_id> <provider_id> [model] [group_id|sid]"
            )
        if args[2].lower() == "add":
            if len(args) < 5:
                return event.plain_result(
                    "Usage: /router lock add <sender_id> <provider_id> [model] [group_id|sid]"
                )
            sender_id = args[3]
            provider_id = args[4]
            model_name = args[5] if len(args) >= 6 else ""
            group_id = args[6] if len(args) >= 7 else ""
            locks = self.config.setdefault("user_model_locks", [])
            locks.append(
                {
                    "rule_enabled": True,
                    "rule_name": f"lock_{sender_id}",
                    "sender_id": sender_id,
                    "group_id": group_id,
                    "provider_id": provider_id,
                    "model_name": model_name,
                }
            )
            self._save_config()
            return event.plain_result(
                f"Locked {sender_id} -> {provider_id}:{model_name or 'default'}"
            )

        if len(args) < 4:
            return event.plain_result(
                "Usage: /router lock remove <sender_id> [group_id|sid]"
            )
        sender_id = args[3]
        group_id = args[4] if len(args) >= 5 else ""
        locks = self.config.get("user_model_locks", [])
        before = len(locks)
        self.config["user_model_locks"] = [
            item
            for item in locks
            if not (
                isinstance(item, dict)
                and str(item.get("sender_id", "")) == sender_id
                and str(item.get("group_id", "")) == group_id
            )
        ]
        self._save_config()
        return event.plain_result(
            f"Removed {before - len(self.config['user_model_locks'])} lock(s)."
        )
