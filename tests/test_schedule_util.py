import unittest
from datetime import datetime, timedelta, timezone

from multi_loop import compute_next_run, parse_schedule
from multi_loop.schedule_util import HAS_CRONITER, parse_duration


class ParseScheduleTests(unittest.TestCase):
    def test_interval(self):
        parsed = parse_schedule("every 30m")
        self.assertEqual(parsed["kind"], "interval")
        self.assertEqual(parsed["minutes"], 30)

    def test_interval_hours_and_days(self):
        self.assertEqual(parse_schedule("every 2h")["minutes"], 120)
        self.assertEqual(parse_schedule("every 1d")["minutes"], 1440)

    def test_duration_is_one_shot(self):
        parsed = parse_schedule("90m")
        self.assertEqual(parsed["kind"], "once")
        self.assertEqual(parsed["minutes"], 90)

    def test_iso_timestamp_is_one_shot(self):
        parsed = parse_schedule("2030-01-02T03:04:00+00:00")
        self.assertEqual(parsed["kind"], "once")
        self.assertIn("run_at", parsed)

    def test_invalid_expression_raises(self):
        with self.assertRaises(ValueError):
            parse_schedule("sometimes soon")

    def test_zero_duration_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("0m")

    def test_cron_expression(self):
        if HAS_CRONITER:
            parsed = parse_schedule("0 9 * * *")
            self.assertEqual(parsed["kind"], "cron")
            self.assertEqual(parsed["expr"], "0 9 * * *")
        else:
            with self.assertRaises(ValueError):
                parse_schedule("0 9 * * *")


class ComputeNextRunTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)

    def test_interval_first_run_is_now_plus_interval(self):
        next_run = compute_next_run("every 2h", now=self.now)
        self.assertEqual(datetime.fromisoformat(next_run), self.now + timedelta(hours=2))

    def test_interval_anchors_to_last_run(self):
        last = (self.now - timedelta(minutes=10)).isoformat()
        next_run = compute_next_run("every 30m", now=self.now, last_run_at=last)
        expected = self.now - timedelta(minutes=10) + timedelta(minutes=30)
        self.assertEqual(datetime.fromisoformat(next_run), expected)

    def test_one_shot_returns_none_after_running(self):
        self.assertIsNone(compute_next_run("15m", now=self.now, last_run_at=self.now.isoformat()))

    def test_one_shot_timestamp_returns_target(self):
        next_run = compute_next_run("2030-01-02T03:04:00+00:00", now=self.now)
        self.assertEqual(
            datetime.fromisoformat(next_run),
            datetime(2030, 1, 2, 3, 4, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
