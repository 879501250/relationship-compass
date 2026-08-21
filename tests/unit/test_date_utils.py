"""Central ISO 8601 utility tests."""

import unittest

from tests.support import SCRIPTS  # noqa: F401
from date_utils import add_days_iso, normalize_iso8601


class DateUtilsTests(unittest.TestCase):
    def test_normalize_requires_timezone(self) -> None:
        with self.assertRaises(ValueError):
            normalize_iso8601("2026-08-21T14:00:00")

    def test_normalize_and_add_days(self) -> None:
        normalized = normalize_iso8601("2026-08-21T14:00:00+08:00")
        self.assertEqual(normalized, "2026-08-21T06:00:00+00:00")
        self.assertEqual(add_days_iso(normalized, 14), "2026-09-04T06:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
