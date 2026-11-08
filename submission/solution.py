"""bank-gpt submission: build() -> KnowledgeSystem with ingest / answer / conflicts.

Deterministic v1 (no network, no API keys): a versioned chunk store over every snapshot,
authority + recency + qualifier-aware ranking, `as_of` snapshot selection, an honest refusal,
and conflicts computed at ingest. See NOTES.md for the currency rules.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:  # the scorer puts harness/ on sys.path; fall back to walking up to find it
    from contract import Answer, Conflict, Snapshot, Source  # type: ignore
except ImportError:  # pragma: no cover - exercised only outside the harness
    _h = next((d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists()), None)
    if _h is None:
        raise
    sys.path.insert(0, str(_h))
    from contract import Answer, Conflict, Snapshot, Source  # type: ignore

from bankgpt.answer import Answerer  # noqa: E402
from bankgpt.conflicts import ConflictFinder  # noqa: E402
from bankgpt.store import Store  # noqa: E402
from bankgpt.text import as_of_from_text  # noqa: E402


class BankKnowledge:
    def __init__(self) -> None:
        self.store = Store()
        self.answerer = Answerer(self.store)
        self.finder = ConflictFinder(self.store)
        self._conflicts: list = []
        self._cache: dict = {}

    def ingest(self, snapshot: Snapshot) -> None:
        self.store.add_snapshot(snapshot.snapshot_id, snapshot.taken_on, snapshot.pages)
        self._conflicts = self.finder.compute()
        self._cache.clear()

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        as_of = as_of or as_of_from_text(question)
        si = self.store.snapshot_for(as_of)
        key = (" ".join(question.split()).lower(), si)
        if key not in self._cache:
            self._cache[key] = self.answerer.answer(question, si)
        r = self._cache[key]
        return Answer(text=r.text, sources=[Source(url=u, snapshot_id=sid, quote=q) for u, sid, q in r.sources],
                      confident=r.confident, conflict_note=r.conflict_note)

    def conflicts(self) -> list[Conflict]:
        return [Conflict(topic=c.topic, values=list(c.values),
                         sources=[Source(url=u, snapshot_id=sid, quote=q) for u, sid, q in c.sources],
                         resolution=c.resolution, reason=c.reason) for c in self._conflicts]


def build() -> BankKnowledge:
    return BankKnowledge()
