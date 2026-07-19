import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_model_router.routing import RuleMatcher  # noqa: E402


class RuleMatcherTests(unittest.TestCase):
    def test_first_matching_rule_wins(self):
        matcher = RuleMatcher(
            [
                {
                    "__template_key": "keyword_rule",
                    "rule_name": "code",
                    "keywords": ["Python", "Rust"],
                    "match_mode": "any",
                    "provider_id": "code-provider",
                    "model_name": "code-model",
                },
                {
                    "__template_key": "regex_rule",
                    "rule_name": "fallback-regex",
                    "pattern": "python",
                    "provider_id": "other-provider",
                },
            ]
        )

        decision = matcher.match("写一段 python", "42", {"group-1"})

        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule_name, "code")
        self.assertEqual(decision.provider_id, "code-provider")

    def test_keyword_all_and_case_sensitivity(self):
        matcher = RuleMatcher(
            [
                {
                    "__template_key": "keyword_rule",
                    "keywords": ["Hello", "WORLD"],
                    "match_mode": "all",
                    "case_sensitive": False,
                    "provider_id": "provider",
                }
            ]
        )

        self.assertIsNotNone(matcher.match("hello, world", "", set()))
        self.assertIsNone(matcher.match("hello", "", set()))

    def test_user_and_scope_must_both_match(self):
        matcher = RuleMatcher(
            [
                {
                    "__template_key": "user_rule",
                    "sender_id": "10001",
                    "scope_id": "group-1",
                    "provider_id": "private-provider",
                }
            ]
        )

        self.assertIsNotNone(matcher.match("anything", "10001", {"group-1"}))
        self.assertIsNone(matcher.match("anything", "10002", {"group-1"}))
        self.assertIsNone(matcher.match("anything", "10001", {"group-2"}))

    def test_invalid_regex_warns_and_continues(self):
        warnings = []
        matcher = RuleMatcher(
            [
                {
                    "__template_key": "regex_rule",
                    "rule_name": "broken",
                    "pattern": "[",
                    "provider_id": "bad-provider",
                },
                {
                    "__template_key": "regex_rule",
                    "rule_name": "working",
                    "pattern": "hello",
                    "provider_id": "good-provider",
                },
            ],
            warning_handler=warnings.append,
        )

        decision = matcher.match("hello", "", set())

        self.assertEqual(decision.rule_name, "working")
        self.assertEqual(len(warnings), 1)
        self.assertIn("broken", warnings[0])

    def test_disabled_or_targetless_rules_are_ignored(self):
        matcher = RuleMatcher(
            [
                {
                    "__template_key": "keyword_rule",
                    "rule_enabled": False,
                    "keywords": ["hello"],
                    "provider_id": "disabled",
                },
                {
                    "__template_key": "keyword_rule",
                    "keywords": ["hello"],
                    "provider_id": "",
                },
            ]
        )

        self.assertIsNone(matcher.match("hello", "", set()))


if __name__ == "__main__":
    unittest.main()
