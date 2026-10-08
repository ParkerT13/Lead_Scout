import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.parser import parse_result, is_valid_contact
from scraper.query_builder import build_query_plan
from emailer.generator import clean_name


class ParserTests(unittest.TestCase):
    URL = "https://www.linkedin.com/in/x"

    def parse(self, title):
        return parse_result(title, self.URL, "Houston, Texas")

    def test_basic(self):
        c = self.parse("Frank Sattler - Senior Geologist at Ring Energy | LinkedIn")
        self.assertEqual((c["first_name"], c["last_name"]), ("Frank", "Sattler"))
        self.assertEqual(c["title"], "Senior Geologist at Ring Energy".split(" at ")[0])
        self.assertTrue(is_valid_contact(c, "Ring Energy"))

    def test_credentials_stripped(self):
        c = self.parse("Dr. Jane Q. Public, PhD, PE - Reservoir Engineer - Ring Energy, Inc. | LinkedIn")
        self.assertEqual(c["name"], "Jane Public")

    def test_company_as_title_rejected(self):
        c = self.parse("Pierre Dupont - ConocoPhillips Energy | LinkedIn")
        self.assertFalse(is_valid_contact(c, "Ring Energy"))

    def test_intern_rejected(self):
        c = self.parse("Sam Lee - Geology Intern at Ring Energy | LinkedIn")
        self.assertFalse(is_valid_contact(c, "Ring Energy"))


class QueryTests(unittest.TestCase):
    def test_plan_is_broad_first_and_flat_matches(self):
        plan = build_query_plan("Ring Energy")
        self.assertIn('"geologist"', plan[0][0])
        self.assertEqual(len(plan[0]), len(set(plan[0])))


class NameTests(unittest.TestCase):
    def test_accents_and_punct(self):
        self.assertEqual(clean_name("José"), "jose")
        self.assertEqual(clean_name("Muñoz"), "munoz")
        self.assertEqual(clean_name("John (Jack)"), "john")
        self.assertEqual(clean_name("O'Brien"), "obrien")


if __name__ == "__main__":
    unittest.main()
