import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scraper.fit import classify, annotate


class FitTests(unittest.TestCase):
    def test_core_buyers_and_users_score_high(self):
        for title in ("Senior Geologist", "Geophysicist", "VP Exploration", "Reservoir Engineer",
                      "Completions Engineer", "Director of Geoscience", "Petrophysicist",
                      "Exploration Manager", "Chief Geoscientist"):
            self.assertEqual(classify(title)["fit"], "High", title)

    def test_non_technical_roles_score_low(self):
        for title in ("Controller", "Chief Financial Officer", "Administrative Assistant",
                      "Land Manager", "Accounts Payable Specialist", "HR Manager"):
            self.assertIn(classify(title)["fit"], ("Low",), title)

    def test_unknown_titles_stay_in_the_list_for_review(self):
        self.assertEqual(classify("")["discipline"], "Unknown")
        self.assertEqual(classify("")["fit"], "Medium")
        self.assertEqual(classify("Vice President")["fit"], "Medium")

    def test_seniority_ladder(self):
        order = [classify(t)["seniority"] for t in
                 ("Chief Executive Officer", "Senior Vice President, Exploration", "Director of Engineering",
                  "Operations Manager", "Senior Geologist", "Geologist")]
        self.assertEqual(order, [1, 2, 3, 4, 5, 6])

    def test_annotate_adds_fields_and_removes_nothing(self):
        c = annotate({"name": "A B", "title": "VP Geoscience", "company": "X"})
        self.assertEqual((c["discipline"], c["fit"], c["priority"]), ("Geoscience", "High", "2 - VP"))
        self.assertEqual(c["name"], "A B")

    def test_geoscience_beats_engineering_keywords(self):
        self.assertEqual(classify("Geoscience Engineer")["discipline"], "Geoscience")
        self.assertEqual(classify("Drilling Engineer")["discipline"], "Reservoir/Completions Eng")


if __name__ == "__main__":
    unittest.main()
