from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT.parent))

from astrbot_plugin_model_router.main import ModelRouterPlugin, extract_recent_contexts
from astrbot_plugin_model_router.routing import IntentRouter


def test_extracts_conversation_history_json_shape_and_configured_limits():
    history = [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": [{"type": "text", "text": "123456"}]},
        {"role": "assistant", "content": "latest"},
    ]
    assert extract_recent_contexts(history, context_turns=1, max_chars=4) == [
        {"role": "user", "content": "1234"},
        {"role": "assistant", "content": "late"},
    ]


def test_tier_selection_respects_configured_max_scores():
    plugin = object.__new__(ModelRouterPlugin)
    plugin.config = {
        "tier_low": {"max_score": 3, "global_provider": "low"},
        "tier_mid": {"max_score": 7, "global_provider": "mid"},
        "tier_high": {"global_provider": "high"},
    }
    assert plugin.get_target_config("chat", 3) == ("low", "", "low")
    assert plugin.get_target_config("chat", 7) == ("mid", "", "mid")
    assert plugin.get_target_config("chat", 8) == ("high", "", "high")


def test_cache_key_changes_when_active_task_snapshots_change():
    router = object.__new__(IntentRouter)
    contexts = [{"role": "user", "content": "continue"}]
    first = router._get_cache_key("继续", contexts, [{"id": "a", "score": 4}])
    second = router._get_cache_key("继续", contexts, [{"id": "b", "score": 8}])
    assert first != second


def test_router_refusal_falls_back_without_raising():
    provider = SimpleNamespace(
        text_chat=lambda **_kwargs: _async_result(
            SimpleNamespace(completion_text="I'm sorry, but I can't help with that.")
        )
    )
    context = SimpleNamespace(get_provider_by_id=lambda _provider_id: provider)
    router = IntentRouter(
        context, {"router_config": {"router_provider": "router", "router_timeout": 3}}
    )
    assert asyncio.run(router.analyze_intent("test")) is None


async def _async_result(value):
    return value
