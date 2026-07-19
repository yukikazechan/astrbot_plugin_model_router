import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import astrbot  # noqa: F401
except ModuleNotFoundError:
    raise unittest.SkipTest("AstrBot runtime is not available")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_model_router.main import ModelRouterPlugin  # noqa: E402


class FakeContext:
    def __init__(self, providers=("code", "default")):
        self.providers = set(providers)

    def get_provider_by_id(self, provider_id):
        return object() if provider_id in self.providers else None


class FakeEvent:
    is_at_or_wake_command = True
    unified_msg_origin = "qq:group:200"

    def __init__(self, text, sender_id="1", *, extras=None):
        self.message_str = text
        self.sender_id = sender_id
        self.extras = dict(extras or {})
        self.selection = None

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return "200"

    def get_selected_provider(self):
        return self.extras.get("selected_provider")

    def get_selected_model(self):
        return self.extras.get("selected_model")

    def get_extra(self, key):
        return self.extras.get(key)

    def set_selected_provider(self, provider_id, model_name):
        self.selection = (provider_id, model_name)
        self.extras["selected_provider"] = provider_id
        self.extras["selected_model"] = model_name


class ModelRouterHookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = {
            "plugin_enabled": True,
            "default_provider_id": "default",
            "default_model_name": "base",
            "routing_rules": [
                {
                    "__template_key": "keyword_rule",
                    "rule_name": "code",
                    "keywords": ["python"],
                    "provider_id": "code",
                    "model_name": "coder",
                }
            ],
        }

    async def test_keyword_rule_sets_event_override(self):
        plugin = ModelRouterPlugin(FakeContext(), self.config)
        event = FakeEvent("请写 Python")

        await plugin.route_llm_request(event)

        self.assertEqual(event.selection, ("code", "coder"))

    async def test_provider_request_model_is_not_overridden(self):
        plugin = ModelRouterPlugin(FakeContext(), self.config)
        request = SimpleNamespace(model="handler-model")
        event = FakeEvent("请写 Python", extras={"provider_request": request})

        await plugin.route_llm_request(event)

        self.assertIsNone(event.selection)
        self.assertEqual(request.model, "handler-model")

    async def test_existing_event_selection_is_not_overridden(self):
        plugin = ModelRouterPlugin(FakeContext(), self.config)
        event = FakeEvent(
            "请写 Python",
            extras={"selected_provider": "external", "selected_model": "external-model"},
        )

        await plugin.route_llm_request(event)

        self.assertIsNone(event.selection)

    async def test_unavailable_rule_provider_uses_default(self):
        plugin = ModelRouterPlugin(FakeContext(providers=("default",)), self.config)
        event = FakeEvent("请写 Python")

        await plugin.route_llm_request(event)

        self.assertEqual(event.selection, ("default", "base"))
