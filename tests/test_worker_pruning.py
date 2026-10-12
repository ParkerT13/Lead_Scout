import os, sys, tempfile, unittest
from pathlib import Path

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_wp_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper import worker as sw
from scraper import query_stats
from scraper.query_builder import build_query_plan_kinds, build_query_plan


def person(i):
    return {"href": f"https://www.linkedin.com/in/p{i}", "body": "Houston, Texas",
            "title": f"Person{chr(65 + i % 26)}{chr(97 + i // 26)} Smith - Senior Geologist at Ring Energy | LinkedIn"}


def make_worker(results_by_query, **kw):
    w = sw.SearchWorker(["Ring Energy"], Path("."), sources={"team": False, "sec": False, "web": False}, **kw)
    calls = []

    def fake(query):
        calls.append(query)
        return results_by_query(len(calls), query)
    w._search = fake
    return w, calls


class PlanTests(unittest.TestCase):
    def test_kinds_cover_every_query_once(self):
        plan = build_query_plan_kinds("Ring Energy")
        flat = [q for g in plan for _, q in g]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual({k for g in plan for k, _ in g}, {"core", "narrow", "wide", "city"})
        self.assertEqual(sorted(q for g in plan for _, q in g), sorted(q for g in build_query_plan("Ring Energy") for q in g))
        self.assertEqual(plan[0][0][0], "core")


class PruningTests(unittest.TestCase):
    def setUp(self):
        p = query_stats._path()
        if p.exists():
            p.unlink()

    def test_dry_streak_stops_each_kind(self):
        # one person on the first query, nothing afterwards
        w, calls = make_worker(lambda n, q: [person(0)] if n == 1 else [])
        w._process_company("Ring Energy")
        full = sum(len(g) for g in build_query_plan_kinds("Ring Energy"))
        # first variant: 1 + 12 core, 8 narrow, 6 wide, 4 city dry queries; later variants add only a few
        self.assertLess(len(calls), full * 0.6)       # roughly half of the full plan when nothing pays off
        self.assertGreater(len(calls), 20)

    def test_thorough_runs_everything(self):
        w, calls = make_worker(lambda n, q: [person(0)] if n == 1 else [], thorough=True)
        w._process_company("Ring Energy")
        full = sum(len(g) for g in build_query_plan_kinds("Ring Energy"))
        self.assertEqual(len(calls), full)

    def test_historically_useless_kind_is_skipped_but_core_never(self):
        path = query_stats._path()
        path.write_text('{"city": {"q": 500, "new": 2}, "core": {"q": 500, "new": 0}}', encoding="utf-8")
        w, calls = make_worker(lambda n, q: [person(n)] if n <= 40 else [])
        w._process_company("Ring Energy")
        self.assertFalse([q for q in calls if q.endswith(" Houston")])          # city kind pruned
        self.assertTrue([q for q in calls if '"geologist"' in q])               # core still searched

    def test_stats_are_recorded_and_merged_across_runs(self):
        w, _ = make_worker(lambda n, q: [person(n)] if n <= 3 else [])
        w._process_company("Ring Energy")
        w2, _ = make_worker(lambda n, q: [])
        w2._process_company("Ring Energy")
        stats = query_stats.QueryStats()
        self.assertGreater(stats.base["core"]["q"], 12)
        self.assertGreaterEqual(stats.base["core"]["new"], 3)

    def test_people_found_by_search_are_emitted_once(self):
        got = []
        w, _ = make_worker(lambda n, q: [person(0)])          # same person on every query
        w.contact_found.connect(got.append)
        w._process_company("Ring Energy")
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()
