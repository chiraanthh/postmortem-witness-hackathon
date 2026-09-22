"""Model-picker runtime overrides must not survive a fresh start."""

from __future__ import annotations

import unittest

from backend import config


class TestExtractionRuntimeReset(unittest.TestCase):
    def tearDown(self) -> None:
        config.reset_extraction_runtime()

    def test_reset_clears_picker_overrides(self) -> None:
        config.set_extraction_runtime(
            provider="assemblyai_gateway",
            model="claude-sonnet-4-5-20250929",
            cleanup=True,
        )
        before = config.extraction_runtime_status()
        self.assertEqual(before["provider"], "assemblyai_gateway")
        self.assertTrue(before["cleanup_enabled"])

        after = config.reset_extraction_runtime()
        self.assertEqual(after["provider"], config.EXTRACTION_PROVIDER)
        self.assertEqual(after["model"], config.EXTRACTION_MODEL)
        self.assertFalse(after["cleanup_enabled"])
        self.assertEqual(config.extraction_provider(), config.EXTRACTION_PROVIDER)
        self.assertEqual(config.extraction_model(), config.EXTRACTION_MODEL)


if __name__ == "__main__":
    unittest.main()
