import os, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import searchcore as sc


def fresh():
    """Isolated cache / limiter / health for each test."""
    sc.CACHE = sc.SearchCache(Path(tempfile.mkdtemp(prefix="ls_sc_")) / "c.sqlite")
    sc.LIMITER = sc.RateLimiter(min_interval=0.0)
    sc.HEALTH = sc.EngineHealth()


class SearchTests(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_second_call_is_served_from_cache(self):
        calls = []
        fn = lambda q: calls.append(q) or [{"title": "t", "href": "h"}]
        a = sc.search("q1", [("DDG", fn)])
        b = sc.search("q1", [("DDG", fn)])
        self.assertEqual(len(calls), 1)
        self.assertEqual(a[0]["_engine"], "DDG")
        self.assertEqual(b[0]["_engine"], "DDG")

    def test_empty_results_are_not_cached(self):
        calls = []
        fn = lambda q: calls.append(q) or []
        sc.search("q2", [("DDG", fn)])
        sc.search("q2", [("DDG", fn)])
        self.assertEqual(len(calls), 2)

    def test_falls_through_to_next_engine(self):
        r = sc.search("q3", [("DDG", lambda q: []), ("Bing", lambda q: [{"href": "x"}])])
        self.assertEqual(r[0]["_engine"], "Bing")

    def test_exceptions_never_escape(self):
        def boom(q):
            raise RuntimeError("x")
        self.assertEqual(sc.search("q4", [("DDG", boom)]), [])

    def test_blocked_engine_is_skipped_after_repeated_failures(self):
        bing_calls = []
        bing = lambda q: bing_calls.append(q) or []
        for i in range(6):
            sc.search(f"q{i}", [("Bing", bing)])
        self.assertEqual(len(bing_calls), 3)               # tripped after 3 empties, then skipped

    def test_breaker_reopens_after_cooldown(self):
        t = {"now": 0.0}
        h = sc.EngineHealth(clock=lambda: t["now"])
        for _ in range(3):
            h.fail("Bing")
        self.assertFalse(h.available("Bing"))
        t["now"] = 1801
        self.assertTrue(h.available("Bing"))

    def test_stop_aborts_before_any_request(self):
        calls = []
        sc.search("q5", [("DDG", lambda q: calls.append(q) or [{"href": "x"}])], stop=lambda: True)
        self.assertEqual(calls, [])


class LimiterTests(unittest.TestCase):
    def test_requests_are_spaced_across_threads(self):
        lim = sc.RateLimiter(min_interval=0.05, jitter=0.0)
        stamps = []

        def worker():
            lim.wait()
            stamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(g >= 0.04 for g in gaps), gaps)


if __name__ == "__main__":
    unittest.main()
