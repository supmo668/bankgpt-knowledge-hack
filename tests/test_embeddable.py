"""R7 embeddable: a separate Python host builds and calls the solution through the documented contract.

The host is a throwaway script written to a temp directory and run in a clean interpreter
(`python -I -S -B`: no PYTHONPATH, no site-packages at all, no inherited environment, cwd outside the repo).
It never imports the organiser harness's runner: it loads `submission/solution.py` by path, feeds it
snapshots built from its OWN duck-typed objects (the kit's synthetic selftest site), and checks the three
contract calls plus the types that come back. The organiser harness itself is pinned byte-for-byte to the
kit import (commit 9be8c8e) and is also run unchanged in a clean interpreter.

Run:  python -m unittest discover -s tests
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELFTEST = ROOT / "harness" / "selftest"

# sha256 of the kit's harness files as imported in 9be8c8e; the organiser harness must stay unchanged
KIT_HARNESS = {
    "contract.py": "f84bab0b19edefed76112b532d2a39f0669f393f736c42785ec22ff609a3956c",
    "run_eval.py": "09d385654c2fdedb2ec856fd8fd48683e2029cee9708957e99ec2917d1c8e59c",
    "scoring.py": "682a40a23a26e306a61c1bc78643f4d120aa4fdd6f05b35c9fecb9edd72a99de",
}

HOST = textwrap.dedent('''
    """A separate host application embedding the bank-gpt solution (stdlib only)."""
    import importlib.util, json, sys
    from pathlib import Path
    from types import SimpleNamespace

    submission, selftest = Path(sys.argv[1]), Path(sys.argv[2])

    def load_snapshots(root):
        out = []
        for d in sorted(p for p in root.iterdir() if (p / "manifest.json").exists()):
            m = json.loads((d / "manifest.json").read_text())
            pages = [SimpleNamespace(url=p["url"], title=p.get("title", ""),
                                     markdown=(d / p["file"]).read_text(encoding="utf-8"),
                                     fetched_at=p.get("fetched_at", m["taken_on"])) for p in m["pages"]]
            out.append(SimpleNamespace(snapshot_id=m["snapshot_id"], taken_on=m["taken_on"], pages=pages))
        return sorted(out, key=lambda s: s.taken_on)

    spec = importlib.util.spec_from_file_location("solution", submission / "solution.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ks = mod.build()                                   # the documented entry point
    empty = ks.answer("What is the 6-month certificate APY?")   # before any ingest: honest refusal
    snaps = load_snapshots(selftest)
    for s in snaps:                                    # oldest first, one call per snapshot
        ks.ingest(s)
    other = mod.build()                                # a second instance shares no state with the first
    other.ingest(snaps[0])

    def ans(a):
        return {"type": type(a).__name__, "text": a.text, "confident": a.confident,
                "conflict_note": a.conflict_note,
                "sources": [{"url": s.url, "snapshot_id": s.snapshot_id} for s in a.sources]}

    conflicts = ks.conflicts()
    foreign = sorted(m.split(".")[0] for m in sys.modules
                     if m.split(".")[0] not in sys.stdlib_module_names
                     and m.split(".")[0] not in ("solution", "bankgpt", "contract", "__main__"))
    print(json.dumps({
        "empty": ans(empty),
        "current": ans(ks.answer("What is the 6-month certificate APY?")),
        "as_of": ans(ks.answer("What was the 6-month certificate APY?", as_of="2026-01-31")),
        "other_instance": ans(other.answer("What is the 6-month certificate APY?")),
        "refuse": ans(ks.answer("What is the routing number for wire transfers to the moon branch?")),
        "conflicts": [{"type": type(c).__name__, "topic": c.topic, "values": list(c.values),
                       "resolution": c.resolution, "n_sources": len(c.sources)} for c in conflicts],
        "conflicts_is_list": isinstance(conflicts, list),
        "foreign_modules": foreign,
    }))
''')


def clean_run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}   # nothing else inherited
    return subprocess.run([sys.executable, "-I", "-S", "-B", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=120)


class OrganiserHarnessUnchanged(unittest.TestCase):
    def test_harness_files_match_the_kit(self):
        for name, digest in KIT_HARNESS.items():
            got = hashlib.sha256((ROOT / "harness" / name).read_bytes()).hexdigest()
            self.assertEqual(got, digest, f"harness/{name} differs from the kit")

    def test_unchanged_harness_scores_the_submission_in_a_clean_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = clean_run([str(ROOT / "harness/run_eval.py"), "--submission", str(ROOT / "submission"),
                           "--snapshots", str(SELFTEST / "snapshots"), "--eval", str(SELFTEST / "eval.jsonl"),
                           "--conflicts-gold", str(SELFTEST / "conflicts_gold.json"), "--out", tmp], cwd=tmp)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            report = json.loads((Path(tmp) / "report.json").read_text())
        self.assertEqual(report["errors"], 0)
        self.assertEqual(report["final_score"], 1.0)


class SeparateHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host_app.py"
            host.write_text(HOST)
            r = clean_run([str(host), str(ROOT / "submission"), str(SELFTEST / "snapshots")], cwd=tmp)
        if r.returncode != 0:
            raise AssertionError(f"host failed:\n{r.stderr[-3000:]}")
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_answer_types_and_fields(self):
        a = self.out["current"]
        self.assertEqual(a["type"], "Answer")
        self.assertTrue(a["confident"])
        self.assertIn("3.50%", a["text"])
        self.assertTrue(a["sources"] and all(s["url"].startswith("https://example-cu.test/") for s in a["sources"]))

    def test_as_of(self):
        self.assertIn("2.95%", self.out["as_of"]["text"])
        self.assertNotIn("3.50%", self.out["as_of"]["text"])

    def test_before_ingest_and_unknown_refuse(self):
        for k in ("empty", "refuse"):
            self.assertFalse(self.out[k]["confident"], k)

    def test_instances_are_independent(self):
        self.assertIn("2.95%", self.out["other_instance"]["text"])   # only the January snapshot was ingested

    def test_conflicts(self):
        self.assertTrue(self.out["conflicts_is_list"])
        cs = self.out["conflicts"]
        self.assertTrue(cs and all(c["type"] == "Conflict" and c["n_sources"] >= 1 for c in cs))
        self.assertTrue(any((c["resolution"] or "").startswith("3.5%") and "2.95%" in " ".join(c["values"]) for c in cs))

    def test_no_third_party_modules(self):
        self.assertEqual(self.out["foreign_modules"], [])


if __name__ == "__main__":
    unittest.main()
