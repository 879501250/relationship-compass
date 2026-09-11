from __future__ import annotations

import unittest

from eval_console.formatting import format_datetime_for_display


class FormattingTests(unittest.TestCase):
    def test_iso_datetime_is_rendered_without_protocol_separator_or_seconds(self) -> None:
        rendered = format_datetime_for_display("2026-09-10T08:02:33Z")
        self.assertNotIn("T", rendered)
        self.assertNotIn("Z", rendered)
        self.assertRegex(rendered, r"^2026-09-10 \d{2}:\d{2}$")

    def test_empty_datetime_has_a_stable_human_placeholder(self) -> None:
        self.assertEqual(format_datetime_for_display(None), "Never")
