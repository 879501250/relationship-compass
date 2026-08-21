"""Landmark and temporary event retention tests."""

from tests.support import DirectMemoryCase


class MemoryRetentionTests(DirectMemoryCase):
    def test_landmarks_survive_while_temporary_events_are_evicted(self) -> None:
        self.enable()
        self.apply(
            self.event_delta(
                "obj-a", "first_meeting", "第一次见面", "2025-01-01T18:00:00+08:00", "landmark"
            )
        )
        self.apply(
            self.event_delta(
                "obj-a",
                "relationship_confirmed",
                "确认关系",
                "2025-02-01T20:00:00+08:00",
                "landmark",
            )
        )
        for day in range(1, 25):
            self.apply(
                self.event_delta(
                    "obj-a",
                    f"temporary_{day}",
                    f"临时事件 {day}",
                    f"2025-03-{day:02d}T12:00:00+08:00",
                    "temporary",
                )
            )

        events = [
            item for item in self.show("obj-a")["memories"] if item["scope"] == "event"
        ]
        fields = {item["field"] for item in events}

        self.assertEqual(len(events), 20)
        self.assertIn("first_meeting", fields)
        self.assertIn("relationship_confirmed", fields)
        self.assertNotIn("temporary_1", fields)
        self.assertIn("temporary_24", fields)


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
