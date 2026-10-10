import csv, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from output import csv_writer as cw

OLD = "name,title,company,location,basin,linkedin_url,source,date_pulled\n"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ls_csv_"))
        self.path = self.dir / "all_contacts.csv"

    def rows(self):
        return list(csv.DictReader(open(self.path, encoding="utf-8")))

    def test_old_rows_and_shifted_rows_repaired(self):
        self.path.write_text(
            OLD
            + 'Grant Magner,TG Natural Resources,Brothers oil & iron,Lafayette,,https://www.linkedin.com/in/gm,DDG,2026-07-31\n'
            # new-layout row appended under the old header (the bug): first,last,title,company,location,basin,url,emp,date,email...
            + 'Tommy,Chatfield,Operations Engineer,Ring Energy,Houston,Permian,https://www.linkedin.com/in/tc,,2026-08-01,,,\n',
            encoding="utf-8")
        shifted = cw.migrate_file(self.path)
        self.assertEqual(shifted, 1)
        r = self.rows()
        self.assertEqual((r[0]["first_name"], r[0]["last_name"], r[0]["company"]), ("Grant", "Magner", "Brothers oil & iron"))
        self.assertEqual((r[1]["first_name"], r[1]["last_name"], r[1]["title"], r[1]["company"]),
                         ("Tommy", "Chatfield", "Operations Engineer", "Ring Energy"))
        self.assertEqual(r[1]["linkedin_url"], "https://www.linkedin.com/in/tc")
        self.assertTrue(list((self.dir / "backup_pre_migration").glob("*.csv")))

    def test_current_layout_untouched(self):
        cw._append_row(self.path, {"first_name": "A", "last_name": "B", "company": "X"})
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(cw.migrate_file(self.path), 0)
        self.assertEqual(before, self.path.read_text(encoding="utf-8"))

    def test_append_after_old_header_stays_aligned(self):
        self.path.write_text(OLD + "Ann Lee,Geologist,Ring Energy,Houston,,https://x/in/a,DDG,2026-07-31\n", encoding="utf-8")
        cw._append_row(self.path, {"first_name": "Bo", "last_name": "Ray", "title": "Engineer",
                                   "company": "Ring Energy", "linkedin_url": "https://x/in/b"})
        r = self.rows()
        self.assertEqual([x["company"] for x in r], ["Ring Energy", "Ring Energy"])
        self.assertEqual([x["last_name"] for x in r], ["Lee", "Ray"])


if __name__ == "__main__":
    unittest.main()
