import unittest
from unittest import mock

from multi_loop import (
    Capability,
    CapabilityRegistry,
    SideEffectClass,
    Toolset,
    default_capabilities,
)


class CapabilityRegistryTests(unittest.TestCase):
    def test_search_finds_matching_default_capability(self):
        registry = default_capabilities()

        matches = registry.search("resume mission schedule")

        self.assertGreaterEqual(len(matches), 1)
        self.assertEqual(matches[0].name, "scheduled_tick")

    def test_duplicate_registration_requires_override(self):
        registry = CapabilityRegistry()
        capability = Capability(
            name="web_research",
            description="Research on the web.",
            toolset_or_backend="web",
            side_effect_class=SideEffectClass.READ_ONLY,
        )

        registry.register(capability)

        with self.assertRaises(ValueError):
            registry.register(capability)

    def test_unavailable_capability_is_hidden_by_default(self):
        registry = CapabilityRegistry()
        registry.register(
            Capability(
                name="paid_ads",
                description="Launch paid advertising campaigns.",
                toolset_or_backend="ads",
                side_effect_class=SideEffectClass.SPEND_MONEY,
                tags=["ads", "campaign"],
            ),
            check=lambda: False,
        )

        self.assertEqual(registry.search("ads"), [])
        self.assertEqual(registry.search("ads", include_unavailable=True)[0].name, "paid_ads")

    def test_describe_returns_card_with_availability(self):
        registry = default_capabilities()

        card = registry.describe("shell_command")

        self.assertEqual(card["name"], "shell_command")
        self.assertTrue(card["available"])
        self.assertEqual(card["side_effect_class"], "local_write")
        self.assertIn("missing_env", card)
        self.assertEqual(card["missing_env"], [])

    def test_requires_env_controls_availability(self):
        registry = CapabilityRegistry()
        registry.register(
            Capability(
                name="hosted_llm",
                description="Call a hosted model.",
                toolset_or_backend="api",
                requires_env=["MULTILOOP_TEST_KEY"],
            )
        )

        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MULTILOOP_TEST_KEY", None)
            self.assertEqual(registry.missing_env("hosted_llm"), ["MULTILOOP_TEST_KEY"])
            self.assertFalse(registry.available("hosted_llm"))

        with mock.patch.dict("os.environ", {"MULTILOOP_TEST_KEY": "secret"}):
            self.assertEqual(registry.missing_env("hosted_llm"), [])
            self.assertTrue(registry.available("hosted_llm"))
            self.assertTrue(registry.describe("hosted_llm")["available"])

    def test_search_cards_returns_structured_dicts(self):
        registry = default_capabilities()

        cards = registry.search_cards("resume mission schedule")

        self.assertGreaterEqual(len(cards), 1)
        self.assertEqual(cards[0]["name"], "scheduled_tick")
        self.assertIn("available", cards[0])


class ToolsetTests(unittest.TestCase):
    def _registry(self) -> CapabilityRegistry:
        registry = CapabilityRegistry()
        for name in ("a", "b", "c"):
            registry.register(Capability(name=name, description=name, toolset_or_backend="x"))
        registry.register_toolset(Toolset(name="ab", capabilities=["a", "b"]))
        registry.register_toolset(Toolset(name="abc", capabilities=["c"], includes=["ab"]))
        return registry

    def test_resolve_expands_includes_in_order(self):
        registry = self._registry()
        self.assertEqual(registry.resolve_names("abc"), ["a", "b", "c"])

    def test_resolve_mixes_names_and_dedups(self):
        registry = self._registry()
        self.assertEqual(registry.resolve_names(["ab", "c", "a"]), ["a", "b", "c"])

    def test_resolve_all_token_returns_every_capability(self):
        registry = self._registry()
        self.assertEqual(set(registry.resolve_names("all")), {"a", "b", "c"})
        self.assertEqual(registry.resolve_names("*"), registry.resolve_names("all"))

    def test_resolve_is_cycle_safe(self):
        registry = self._registry()
        registry.register_toolset(Toolset(name="x", capabilities=["a"], includes=["y"]))
        registry.register_toolset(Toolset(name="y", capabilities=["b"], includes=["x"]))
        self.assertEqual(set(registry.resolve_names("x")), {"a", "b"})

    def test_resolve_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            self._registry().resolve_names("missing")

    def test_resolve_can_drop_unavailable(self):
        registry = default_capabilities()
        # The company toolset bundles future/unavailable capabilities.
        available = registry.resolve("company", include_unavailable=False)
        self.assertEqual(available, [])
        self.assertGreater(len(registry.resolve("company")), 0)

    def test_default_company_toolset_resolves_through_includes(self):
        registry = default_capabilities()
        resolved = registry.resolve_names("company")
        self.assertIn("web_research", resolved)
        self.assertIn("paid_ads", resolved)
        self.assertIn("media_generation", resolved)

    def test_describe_toolset_reports_resolution(self):
        registry = default_capabilities()
        card = registry.describe_toolset("local_workers")
        self.assertEqual(card["name"], "local_workers")
        self.assertIn("agent_loop", card["resolved"])
        self.assertIn("agent_loop", card["available"])


if __name__ == "__main__":
    unittest.main()
