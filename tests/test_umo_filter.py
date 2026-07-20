import unittest

from umo_filter import (
    BLACKLIST_MODE,
    get_active_umo_list,
    get_filter_mode,
    is_umo_allowed,
)


class UmoFilterTests(unittest.TestCase):
    def test_empty_blacklist_allows_every_umo(self):
        self.assertTrue(is_umo_allowed("aiocqhttp:group:123", {}))

    def test_blacklist_rejects_only_exact_match(self):
        config = {
            "filter_type": "blacklist",
            "blacklist": ["aiocqhttp:group:123"],
        }
        self.assertFalse(is_umo_allowed("aiocqhttp:group:123", config))
        self.assertTrue(is_umo_allowed("aiocqhttp:group:1234", config))

    def test_whitelist_allows_only_exact_match(self):
        config = {
            "filter_type": "whitelist",
            "whitelist": ["telegram:private:42"],
        }
        self.assertTrue(is_umo_allowed("telegram:private:42", config))
        self.assertFalse(is_umo_allowed("telegram:private:43", config))

    def test_invalid_mode_falls_back_to_blacklist(self):
        config = {"filter_type": "unexpected", "blacklist": ["blocked"]}
        self.assertEqual(get_filter_mode(config), BLACKLIST_MODE)
        self.assertFalse(is_umo_allowed("blocked", config))

    def test_entries_are_trimmed_and_deduplicated(self):
        config = {
            "filter_type": "whitelist",
            "whitelist": [" umo-1 ", "umo-1", "", None, "umo-2"],
        }
        self.assertEqual(get_active_umo_list(config), ["umo-1", "umo-2"])


if __name__ == "__main__":
    unittest.main()
