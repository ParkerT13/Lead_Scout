import os, sys, tempfile, unittest
from pathlib import Path

os.environ["LEADSCOUT_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ls_crm_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emailer import crm_overlap


class CrmOverlapTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="ls_crmf_")) / "hubspot-contacts.csv"
        self.path.write_text(
            "First Name,Last Name,Email,Company Name\n"
            "Ann,Lee,alee@ring.com,Ring Energy Inc\n"
            "Bob,Ray,,Chord Energy\n", encoding="utf-8")

    def test_matches_by_email_or_by_name_and_company(self):
        idx = crm_overlap.load(self.path)
        rows = [
            {"name": "Someone Else", "email": "ALEE@ring.com", "company": "X"},          # email match
            {"name": "Bob Ray", "company": "chord energy"},                              # name+company match
            {"name": "Bob Ray", "company": "Ring Energy"},                               # other company
            {"name": "New Person", "company": "Ring Energy"},
            {"name": "Ann Lee", "company": "Ring Energy, LLC"},                          # legal suffix differs
        ]
        self.assertEqual(idx.mark(rows), 3)
        self.assertEqual([r["in_crm"] for r in rows], ["yes", "yes", "", "", "yes"])

    def test_missing_export_is_not_an_error(self):
        self.assertIsNone(crm_overlap.load(self.path.parent / "nope.csv"))


if __name__ == "__main__":
    unittest.main()
