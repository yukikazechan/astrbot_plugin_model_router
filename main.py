from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .routing import RouteDecision, RuleMatcher

PLUGIN_VERSION = "1.0.0"


@register(
    "astrbot_plugin_model_router",
    "xkeyC(Fork)",
    "按关键字、正则或用户选择模型",
    PLUGIN_VERSION,
    "https://github.com/xkeyC/astrbot_plugin_model_router",
)
class ModelRouterPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

    @filter.on_waiting_llm_request(priority=-100)
    async def route_llm_request(self, event: AstrMessageEvent):
        """Select a provider/model immediately before the LLM request is built."""
        if not self.config.get("plugin_enabled", True):
            return
        if not event.is_at_or_wake_command:
            return
        if self._has_existing_model_selection(event):
            logger.debug("[ModelRouter] provider/model already selected; skipped")
            return

        decision = self._match_event(event)
        if decision:
            if self._apply_provider_selection(
                event, decision.provider_id, decision.model_name
            ):
                logger.info(
                    "[ModelRouter] matched %s rule=%s provider=%s model=%s",
                    decision.rule_type,
                    decision.rule_name,
                    decision.provider_id,
                    decision.model_name or "default",
                )
                return
            logger.warning(
                "[ModelRouter] matched rule=%s but target provider is unavailable; "
                "trying default",
                decision.rule_name,
            )

        provider_id, model_name = self._get_default_model()
        if decision and provider_id == decision.provider_id:
            return
        if provider_id and self._apply_provider_selection(event, provider_id, model_name):
            logger.info(
                "[ModelRouter] no rule matched; using default provider=%s model=%s",
                provider_id,
                model_name or "default",
            )

    def _match_event(
        self,
        event: AstrMessageEvent,
        text: str | None = None,
    ) -> RouteDecision | None:
        matcher = RuleMatcher(
            self._get_rules(),
            warning_handler=lambda message: logger.warning("[ModelRouter] %s", message),
        )
        return matcher.match(
            text=event.message_str if text is None else text,
            sender_id=str(event.get_sender_id() or ""),
            identifiers=self._get_event_identifiers(event),
        )

    def _get_rules(self) -> list[dict[str, Any]]:
        rules = self.config.get("routing_rules", [])
        return rules if isinstance(rules, list) else []

    def _get_default_model(self) -> tuple[str, str]:
        provider_id = str(self.config.get("default_provider_id", "")).strip()
        model_name = str(self.config.get("default_model_name", "")).strip()
        return provider_id, model_name

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

    @staticmethod
    def _has_existing_model_selection(event: AstrMessageEvent) -> bool:
        provider_request = event.get_extra("provider_request")
        if provider_request is not None and getattr(provider_request, "model", None):
            return True
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

    @staticmethod
    def _get_event_identifiers(event: AstrMessageEvent) -> set[str]:
        identifiers = {str(event.unified_msg_origin)}
        if group_id := event.get_group_id():
            identifiers.add(str(group_id))
        return identifiers

    def _generate_rules_text(self) -> str:
        rules = self._get_rules()
        if not rules:
            return "未配置路由规则。"
        lines = []
        for index, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                continue
            enabled = "启用" if rule.get("rule_enabled", True) else "停用"
            rule_type = RuleMatcher._rule_type(rule) or "unknown"
            name = str(rule.get("rule_name", "")).strip() or f"rule_{index}"
            target = (
                f"{rule.get('provider_id') or '-'}:"
                f"{rule.get('model_name') or 'default'}"
            )
            lines.append(f"{index}. [{enabled}] {name} ({rule_type}) -> {target}")
        return "\n".join(lines) or "未配置路由规则。"

    @filter.command("router")
    async def router_command(self, event: AstrMessageEvent):
        args = event.message_str.split(maxsplit=2)
        if len(args) < 2:
            return event.plain_result(
                "模型路由器命令:\n"
                "/router status - 查看状态\n"
                "/router rules - 查看规则及匹配顺序\n"
                "/router test <文本> - 测试当前用户发送文本时会命中哪条规则"
            )

        sub_cmd = args[1].lower()
        if sub_cmd == "status":
            provider_id, model_name = self._get_default_model()
            return event.plain_result(
                "Model Router Status:\n"
                f"- Enabled: {'Yes' if self.config.get('plugin_enabled', True) else 'No'}\n"
                f"- Rules: {len(self._get_rules())}\n"
                f"- Default: {provider_id or 'unchanged'}:{model_name or 'default'}\n"
                f"- Version: {PLUGIN_VERSION}"
            )
        if sub_cmd in {"rules", "config", "list"}:
            return event.plain_result(self._generate_rules_text())
        if sub_cmd == "test":
            if len(args) < 3 or not args[2].strip():
                return event.plain_result("Usage: /router test <文本>")
            decision = self._match_event(event, args[2].strip())
            if not decision:
                provider_id, model_name = self._get_default_model()
                return event.plain_result(
                    "未命中规则；"
                    f"默认目标: {provider_id or '保持原模型'}:{model_name or 'default'}"
                )
            return event.plain_result(
                f"命中 {decision.rule_type}: {decision.rule_name}\n"
                f"目标: {decision.provider_id}:{decision.model_name or 'default'}"
            )
        return event.plain_result(f"Unknown subcommand: {sub_cmd}\nUse /router for help.")
