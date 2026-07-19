"""Local, deterministic matching for model routing rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RouteDecision:
    rule_name: str
    rule_type: str
    provider_id: str
    model_name: str = ""


class RuleMatcher:
    """Match the first enabled rule in configuration order."""

    def __init__(
        self,
        rules: list[dict[str, Any]] | None,
        warning_handler: Callable[[str], None] | None = None,
    ) -> None:
        self.rules = rules if isinstance(rules, list) else []
        self.warning_handler = warning_handler

    def match(
        self,
        text: str,
        sender_id: str,
        identifiers: set[str] | None = None,
    ) -> RouteDecision | None:
        identifiers = identifiers or set()
        for index, rule in enumerate(self.rules, start=1):
            if not isinstance(rule, dict) or not rule.get("rule_enabled", True):
                continue
            provider_id = str(rule.get("provider_id", "")).strip()
            if not provider_id or not self._scope_matches(rule, identifiers):
                continue

            rule_type = self._rule_type(rule)
            matched = False
            if rule_type == "user_rule":
                matched = self._user_matches(rule, sender_id)
            elif rule_type == "keyword_rule":
                matched = self._keyword_matches(rule, text)
            elif rule_type == "regex_rule":
                matched = self._regex_matches(rule, text, index)

            if matched:
                return RouteDecision(
                    rule_name=str(rule.get("rule_name", "")).strip()
                    or f"rule_{index}",
                    rule_type=rule_type,
                    provider_id=provider_id,
                    model_name=str(rule.get("model_name", "")).strip(),
                )
        return None

    @staticmethod
    def _rule_type(rule: dict[str, Any]) -> str:
        rule_type = str(
            rule.get("__template_key") or rule.get("template") or ""
        ).strip()
        if rule_type:
            return rule_type
        # Allow hand-written configuration without AstrBot's template marker.
        if "sender_id" in rule:
            return "user_rule"
        if "pattern" in rule:
            return "regex_rule"
        if "keywords" in rule:
            return "keyword_rule"
        return ""

    @staticmethod
    def _scope_matches(rule: dict[str, Any], identifiers: set[str]) -> bool:
        scope = str(rule.get("scope_id") or rule.get("group_id") or "").strip()
        return not scope or scope in identifiers

    @staticmethod
    def _user_matches(rule: dict[str, Any], sender_id: str) -> bool:
        target = str(rule.get("sender_id", "")).strip()
        return bool(target and sender_id and target == sender_id)

    @staticmethod
    def _keyword_matches(rule: dict[str, Any], text: str) -> bool:
        raw_keywords = rule.get("keywords", [])
        if not isinstance(raw_keywords, list):
            return False
        keywords = [str(item) for item in raw_keywords if str(item)]
        if not keywords:
            return False
        if not rule.get("case_sensitive", False):
            text = text.casefold()
            keywords = [keyword.casefold() for keyword in keywords]
        matches = [keyword in text for keyword in keywords]
        if str(rule.get("match_mode", "any")).lower() == "all":
            return all(matches)
        return any(matches)

    def _regex_matches(
        self,
        rule: dict[str, Any],
        text: str,
        index: int,
    ) -> bool:
        pattern = str(rule.get("pattern", ""))
        if not pattern:
            return False
        flags = 0 if rule.get("case_sensitive", False) else re.IGNORECASE
        try:
            return re.search(pattern, text, flags) is not None
        except re.error as exc:
            if self.warning_handler:
                name = str(rule.get("rule_name", "")).strip() or f"rule_{index}"
                self.warning_handler(f"invalid regex in {name}: {exc}")
            return False
