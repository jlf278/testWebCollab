import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cabanas.core import Config, MIN_INTERVAL, Monitor, Offer, Retry, Snapshot, State, Stop, Target, eligible, locked

NOW = datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()


class FakeBrowser:
    def __init__(self):
        self.reserved = set()
        self.prepared = []
        self.submissions = 0
        self.failure = None

    def snapshot(self, target):
        return Snapshot(target.day in self.reserved, [Offer("Family Cabana", 0), Offer("Serenity Bay Cabana", 1)])

    def prepare(self, target, offer):
        self.prepared.append((target, offer))

    def submit(self):
        self.submissions += 1
        if self.failure:
            raise self.failure
        self.reserved.add(self.prepared[-1][0].day)
        return "fixture-confirmation"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.config = Config([Target(day, port, "fixture") for day, port in [
            ("2026-12-22", "GOC"), ("2026-12-23", "LPT"), ("2026-12-26", "GOC")]],
            1800, {}, ["Family Cabana"], self.base / "session", self.base / "state", "", "test")
        self.state = State(self.config.state, self.config.scope)
        self.browser = FakeBrowser()
        self.now = NOW
        self.monitor = Monitor(self.config, self.state, self.browser, clock=lambda: self.now)

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_books_uncovered_days_only_and_reconciles_each_cycle(self):
        self.browser.reserved.add("2026-12-22")
        self.monitor.check(book=True)
        self.assertEqual(self.browser.submissions, 2)
        self.now += 1800
        self.monitor.check(book=True)
        self.assertEqual(self.browser.submissions, 2)

    def test_observation_never_submits(self):
        self.monitor.check()
        self.assertEqual(self.browser.prepared, [])
        self.assertEqual(self.browser.submissions, 0)

    def test_persisted_interval_survives_restarting_monitor(self):
        self.monitor.check()
        self.state.close()
        self.state = State(self.config.state, self.config.scope)
        second = Monitor(self.config, self.state, self.browser, clock=lambda: self.now)
        self.assertIn("Waiting", second.check(book=True)[0])
        self.assertEqual(self.browser.submissions, 0)

    def test_uncertain_submit_never_retried_after_resume(self):
        self.browser.failure = TimeoutError("sensitive internal browser data")
        with self.assertRaises(Stop):
            self.monitor.check(book=True)
        self.assertEqual(self.state.get("2026-12-22"), "pending")
        self.assertNotIn("sensitive", self.state.get("paused"))
        self.state.put("paused", "")
        self.now += 1800
        with self.assertRaisesRegex(Stop, "uncertain"):
            self.monitor.check(book=True)
        self.assertEqual(self.browser.submissions, 1)

    def test_uncertain_submit_reconciles_when_live_reservation_exists(self):
        self.state.put("2026-12-22", "pending")
        self.browser.reserved.add("2026-12-22")
        self.monitor.check(book=True)
        self.assertEqual(self.state.get("2026-12-22"), "covered")
        self.assertEqual(self.browser.submissions, 2)

    def test_canceled_reservation_can_be_replaced(self):
        self.state.put("2026-12-22", "covered")
        self.monitor.check(book=True)
        self.assertEqual(self.browser.submissions, 3)

    def test_retry_after_and_exponential_backoff(self):
        self.browser.snapshot = lambda _: (_ for _ in ()).throw(Retry(14400))
        self.monitor.check()
        self.assertEqual(float(self.state.get("next_check")), self.now + 14400)
        self.assertEqual(self.state.get("failures"), "1")

    def test_five_failures_pause(self):
        self.browser.snapshot = lambda _: (_ for _ in ()).throw(Retry())
        for _ in range(5):
            self.monitor.check()
            self.now = float(self.state.get("next_check"))
        self.assertTrue(self.state.get("paused"))

    def test_security_challenge_pauses_without_submitting(self):
        self.browser.snapshot = lambda _: (_ for _ in ()).throw(Stop("Security challenge"))
        with self.assertRaises(Stop):
            self.monitor.check(book=True)
        self.assertEqual(self.browser.submissions, 0)
        self.assertEqual(self.state.get("paused"), "Security challenge")

    def test_end_date_uses_eastern_time(self):
        target = self.config.targets[0]
        self.assertTrue(target.open(datetime(2026, 12, 22, 4, 59, tzinfo=timezone.utc).timestamp()))
        self.assertFalse(target.open(datetime(2026, 12, 22, 5, 0, tzinfo=timezone.utc).timestamp()))

    def test_file_lock_rejects_second_worker(self):
        lock = self.base / "process.lock"
        with locked(lock):
            with self.assertRaises(Stop):
                with locked(lock):
                    pass

    def test_adult_cabanas_excluded(self):
        for title in ["18+ Cabana", "Adults Only Cabana", "Serenity Bay Cabana", "Adult-exclusive Cabana", "Cabana ages 18 and up", "Adults Exclusive Cabana"]:
            self.assertFalse(eligible(title), title)
        self.assertTrue(eligible("Family Beach Cabana"))


class ConfigTests(unittest.TestCase):
    def test_target_urls_and_minimum_interval(self):
        raw = json.loads(Path("config.example.json").read_text())
        for target in raw["targets"]:
            target["url"] = target["url"].replace("YOUR_BOOKING_ID", "12345678")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps(raw))
            self.assertEqual(len(Config.load(path).targets), 3)
            raw["interval_seconds"] = 1
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                Config.load(path)
            raw["interval_seconds"] = MIN_INTERVAL
            raw["targets"][0]["url"] = raw["targets"][0]["url"].replace("disneycruise.disney.go.com", "example.com")
            path.write_text(json.dumps(raw))
            with self.assertRaises(ValueError):
                Config.load(path)


if __name__ == "__main__":
    unittest.main()
