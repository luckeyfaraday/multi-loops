import unittest

from multi_loop import Capability, CapabilityRegistry, SideEffectClass, default_capabilities


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


if __name__ == "__main__":
    unittest.main()
