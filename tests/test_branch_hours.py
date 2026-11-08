"""R8 extensibility scenario: a new fact family (branch hours with a location and an effective date).

Synthetic site only (no snapshot data, no public-eval questions). Four cases from the draft PVR: a current
answer, a historical change, a different branch, and missing evidence, plus how the change shows in
conflicts(). The files this family touched are listed in NOTES.md ("Extending: a new fact family").

Run:  python -m unittest discover -s tests
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from contract import Page, Snapshot  # noqa: E402

import solution  # noqa: E402
from bankgpt.chunker import Chunk, hours_cells  # noqa: E402
from bankgpt.store import row_key, row_values  # noqa: E402

CU = "https://branches.example-cu.test"

HOURS_TABLE = """| Day | Lobby | Drive-up |
| --- | --- | --- |
| Monday-Friday | {wk_lobby} | {wk_drive} |
| Saturday | {sat_lobby} | {sat_drive} |

Hours effective as of {eff}.
"""


def branch_page(name: str, address: str, eff: str, wk_lobby: str, wk_drive: str, sat_lobby: str,
                sat_drive: str) -> str:
    return (f"# {name}\n\n{address}\n\nLobby and drive-up hours\n\n"
            + HOURS_TABLE.format(eff=eff, wk_lobby=wk_lobby, wk_drive=wk_drive, sat_lobby=sat_lobby,
                                 sat_drive=sat_drive))


MAIN_OLD = dict(wk_lobby="9:00 a.m.-5:00 p.m.", wk_drive="8:00 a.m.-6:00 p.m.",
                sat_lobby="9:00 a.m.-12:00 p.m.", sat_drive="8:00 a.m.-12:00 p.m.")
MAIN_NEW = dict(MAIN_OLD, wk_lobby="8:30 a.m.-5:30 p.m.", sat_lobby="9:00 a.m.-1:00 p.m.")
OAK = dict(wk_lobby="10:00 a.m.-6:00 p.m.", wk_drive="7:30 a.m.-6:30 p.m.",
           sat_lobby="Closed", sat_drive="8:00 a.m.-11:00 a.m.")

PAGES_V1 = {
    "/locations/main-street": branch_page("Main Street Branch", "123 Main Street, Springfield", "January 2, 2026",
                                          **MAIN_OLD),
    "/locations/oak-ridge": branch_page("Oak Ridge Branch", "45 Oak Ridge Road, Springfield", "January 2, 2026",
                                        **OAK),
    # a branch whose page carries no hours at all: questions about its hours must be refused
    "/locations/pine-hill": "# Pine Hill Branch\n\n9 Pine Hill Lane, Springfield\n\n"
                            "Pine Hill Branch offers a full-service lobby and a coin machine.\n",
}
PAGES_V2 = dict(PAGES_V1)
PAGES_V2["/locations/main-street"] = branch_page("Main Street Branch", "123 Main Street, Springfield",
                                                 "April 6, 2026", **MAIN_NEW)
# the locations index was not updated: it still shows the January hours under the January effective date
PAGES_V2["/locations"] = ("# Locations\n\n## Main Street Branch\n\n"
                          + HOURS_TABLE.format(eff="January 2, 2026", **MAIN_OLD))


def snap(sid: str, taken: str, pages: dict[str, str]) -> Snapshot:
    return Snapshot(sid, taken, [Page(url=CU + path, title=path.rsplit("/", 1)[-1].replace("-", " ").title(),
                                      markdown=md, fetched_at=taken + "T00:00:00Z") for path, md in pages.items()])


def build():
    ks = solution.build()
    ks.ingest(snap("01_2026-01-05_baseline", "2026-01-05", PAGES_V1))
    ks.ingest(snap("02_2026-04-10_update", "2026-04-10", PAGES_V2))
    return ks


class BranchHoursFamily(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ks = build()

    def ask(self, q, as_of=None):
        return self.ks.answer(q, as_of=as_of)

    # 1. current answer: the newest effective date wins, the stale index copy is never quoted
    def test_current_answer(self):
        a = self.ask("What are the Main Street branch lobby hours on weekdays?")
        self.assertTrue(a.confident)
        self.assertIn("8:30 a.m.-5:30 p.m.", a.text)
        self.assertNotIn("9:00 a.m.-5:00 p.m.", a.text)
        self.assertIn(CU + "/locations/main-street", [s.url for s in a.sources])

    def test_current_saturday(self):
        a = self.ask("What are the Main Street branch Saturday lobby hours?")
        self.assertIn("9:00 a.m.-1:00 p.m.", a.text)
        self.assertNotIn("9:00 a.m.-12:00 p.m.", a.text)

    # 2. historical change: as_of (and an "in February 2026" phrase) answer from the January hours
    def test_historical_as_of(self):
        a = self.ask("What were the Main Street branch lobby hours on weekdays?", as_of="2026-02-15")
        self.assertTrue(a.confident)
        self.assertIn("9:00 a.m.-5:00 p.m.", a.text)
        self.assertNotIn("8:30 a.m.-5:30 p.m.", a.text)

    def test_historical_phrase(self):
        a = self.ask("What were the Main Street branch lobby hours on weekdays in February 2026?")
        self.assertIn("9:00 a.m.-5:00 p.m.", a.text)
        self.assertNotIn("8:30", a.text)

    # 3. a different branch: its own hours, never Main Street's, and no cross-branch "conflict"
    def test_different_branch(self):
        a = self.ask("What are the Oak Ridge branch lobby hours on weekdays?")
        self.assertTrue(a.confident)
        self.assertIn("10:00 a.m.-6:00 p.m.", a.text)
        for v in ("8:30 a.m.-5:30 p.m.", "9:00 a.m.-5:00 p.m."):
            self.assertNotIn(v, a.text)
        self.assertEqual([s.url for s in a.sources][:1], [CU + "/locations/oak-ridge"])

    def test_closed_is_a_value(self):
        a = self.ask("What are the Oak Ridge branch drive-up hours on Saturday?")
        self.assertIn("8:00 a.m.-11:00 a.m.", a.text)

    def test_branches_are_different_facts(self):
        main = Chunk(CU + "/locations/main-street", "Monday-Friday: ...", "Main Street Branch", "row",
                     cells={"Day": "Monday-Friday", "Lobby": "9:00 a.m.-5:00 p.m."})
        oak = Chunk(CU + "/locations/oak-ridge", "Monday-Friday: ...", "Oak Ridge Branch > Lobby hours", "row",
                    cells={"Day": "Monday-Friday", "Lobby": "10:00 a.m.-6:00 p.m."})
        index = Chunk(CU + "/locations", "Monday-Friday: ...", "Locations > Main Street Branch", "row",
                      cells={"Day": "Monday - Friday", "Lobby": "9:00 am - 5:00 pm"})
        self.assertNotEqual(row_key(main), row_key(oak))
        self.assertEqual(row_key(main), row_key(index))         # same branch + day on another page = same fact
        self.assertEqual(row_values(main), row_values(index))   # spelling of the times does not matter
        self.assertFalse(any("oak ridge" in c.topic for c in self.ks.conflicts()))

    # 4. missing evidence: refuse, with no times in the text
    def test_branch_without_hours_refuses(self):
        a = self.ask("What are the Pine Hill branch lobby hours?")
        self.assertFalse(a.confident)
        self.assertIsNone(re.search(r"\d", a.text))

    def test_unknown_branch_refuses(self):
        a = self.ask("What are the Elm Court branch hours on Saturday?")
        self.assertFalse(a.confident)
        self.assertIsNone(re.search(r"\d", a.text))

    def test_before_first_snapshot_has_no_index_page(self):
        a = self.ask("What are the Main Street branch lobby hours on weekdays?", as_of="2026-01-05")
        self.assertIn("9:00 a.m.-5:00 p.m.", a.text)

    # conflicts(): the change and the stale index copy are reported, resolved to the newest effective date
    def test_conflicts_report_the_change(self):
        cs = {c.topic: c for c in self.ks.conflicts()}
        c = cs.get("main street branch hours: monday-friday")
        self.assertIsNotNone(c, sorted(cs))
        self.assertIn("8:30am-5:30pm", c.resolution)
        self.assertTrue(any("9:00am-5:00pm" in v for v in c.values))
        self.assertIn("newer effective date", c.reason)
        self.assertIn("/locations", {s.url.replace(CU, "") for s in c.sources})
        self.assertIn("main street branch hours: saturday", cs)


class HoursCellDetection(unittest.TestCase):
    def row(self, **cells):
        return Chunk("https://x.test/p", "r", "Ctx", "row", cells={"Label": "Mon", **cells})

    def test_rate_and_fee_rows_are_not_hours(self):
        self.assertEqual(hours_cells(self.row(APY="3.50%", Term="12 Months")), {})
        self.assertEqual(hours_cells(self.row(Fee="$2.50")), {})
        self.assertEqual(hours_cells(self.row(Term="10-12")), {})          # a bare number range is not a time

    def test_closed_alone_is_not_an_hours_row(self):
        self.assertEqual(hours_cells(self.row(Lobby="Closed")), {})

    def test_time_ranges(self):
        self.assertEqual(hours_cells(self.row(Lobby="8:30 a.m. – 5 p.m.")), {"Lobby": "8:30am-5pm"})
        self.assertEqual(hours_cells(self.row(Lobby="9am to noon", Drive="Closed")),
                         {"Lobby": "9am-noon", "Drive": "closed"})

    def test_month_name_effective_date_only_for_hours_tables(self):
        from bankgpt.chunker import chunk_page
        rates = chunk_page("https://x.test/rates", "Rates",
                           "| Term | APY |\n| --- | --- |\n| 12 Month | 3.50% |\n\nEffective April 6, 2026\n")
        self.assertTrue(all(c.effective is None for c in rates if c.kind == "row"))
        hours = chunk_page("https://x.test/b", "B", "# B Branch\n\n" + HOURS_TABLE.format(eff="April 6, 2026",
                                                                                       **MAIN_OLD))
        self.assertEqual({c.effective for c in hours if c.kind == "row"}, {"4/6/2026"})


if __name__ == "__main__":
    unittest.main()
