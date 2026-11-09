"""Tests for routing LLM requests that were not triggered by a wake word or @ (active replies).

Run from the folder that contains the plugin directory, with AstrBot importable:
    python -m pytest astrbot_plugin_model_router/tests
"""

import asyncio
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT.parent))

from astrbot.core.star.star_handler import EventType, star_handlers_registry  # noqa: E402

from astrbot_plugin_model_router.main import ModelRouterPlugin  # noqa: E402

UMO = "aiocqhttp:GroupMessage:123"


class FakeEvent:
    def __init__(self, text="帮我写个排序算法", wake=False, umo=UMO):
        self.message_str = text
        self.is_at_or_wake_command = wake
        self.unified_msg_origin = umo
        self.extras = {}

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value


class FakeRouter:
    def __init__(self, score):
        self.score = score
        self.calls = []

    async def analyze_intent(self, user_text, contexts=None, task_snapshots=None):
        self.calls.append(user_text)
        return {"difficulty_score": self.score, "category": "chat", "context_relation": "unrelated"}


class FakeConversationManager:
    async def get_curr_conversation_id(self, umo):
        return None


class FakeContext:
    conversation_manager = FakeConversationManager()

    def get_provider_by_id(self, provider_id):
        return object()


def make_plugin(score=2, router_config=None, session_control=None):
    plugin = object.__new__(ModelRouterPlugin)
    plugin.config = {
        "router_config": router_config or {},
        "session_control": session_control or {"filter_type": "blacklist", "blacklist": []},
        "tier_low": {"global_provider": "low-provider"},
        "tier_mid": {"global_provider": "mid-provider"},
        "tier_high": {"global_provider": "high-provider"},
    }
    plugin.router = FakeRouter(score)
    plugin.context = FakeContext()
    plugin.task_snapshots = {}
    return plugin


class ActiveReplyRoutingTests(unittest.TestCase):
    def test_hook_is_registered_before_provider_selection(self):
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnWaitingLLMRequestEvent, only_activated=False
        )
        self.assertIn("route_active_reply", [h.handler_name for h in handlers])

    def test_active_reply_is_routed_by_default(self):
        plugin = make_plugin(score=8)
        event = FakeEvent()
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, ["帮我写个排序算法"])
        self.assertEqual(event.get_extra("selected_provider"), "high-provider")

    def test_switch_off_leaves_active_reply_on_default_model(self):
        plugin = make_plugin(router_config={"route_active_replies": False})
        event = FakeEvent()
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, [])
        self.assertIsNone(event.get_extra("selected_provider"))

    def test_wake_messages_are_not_routed_twice(self):
        plugin = make_plugin()
        event = FakeEvent(wake=True)
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, [])

    def test_existing_provider_selection_is_kept(self):
        plugin = make_plugin()
        event = FakeEvent()
        event.set_extra("selected_provider", "chosen-by-another-plugin")
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, [])
        self.assertEqual(event.get_extra("selected_provider"), "chosen-by-another-plugin")

    def test_blacklisted_session_keeps_default_model(self):
        plugin = make_plugin(session_control={"filter_type": "blacklist", "blacklist": [UMO]})
        event = FakeEvent()
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, [])
        self.assertIsNone(event.get_extra("selected_provider"))

    def test_disabled_plugin_does_not_route_active_replies(self):
        plugin = make_plugin()
        plugin.config["plugin_enabled"] = False
        event = FakeEvent()
        asyncio.run(plugin.route_active_reply(event))
        self.assertEqual(plugin.router.calls, [])

    def test_message_handler_still_routes_only_wake_messages(self):
        plugin = make_plugin(score=2)
        quiet, called = FakeEvent(), FakeEvent(wake=True)
        asyncio.run(plugin.pre_route_message(quiet))
        asyncio.run(plugin.pre_route_message(called))
        self.assertIsNone(quiet.get_extra("selected_provider"))
        self.assertEqual(called.get_extra("selected_provider"), "low-provider")


if __name__ == "__main__":
    unittest.main()
