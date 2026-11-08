"""Conflicts, computed at ingest from the versioned fact store.

Two families:
  * table facts - the same fact key (product + term/tier qualifiers) carrying different values, either on
    different pages of one snapshot (a stale calculator / landing page) or across snapshots (the rate moved).
    Resolution = the value the answer path would give today: latest snapshot, newest effective date, then
    source authority.
  * text facts  - counts and "Nth time" statements in dated press releases / blog posts that disagree.
    Resolution = the most recently published statement.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .store import Store, row_values, values_disagree

RATE_PAGE_KINDS = ("rate_sheet", "product", "landing", "calculator", "document", "home")
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
COUNT_RE = re.compile(r"\b(\d{1,3})\s+((?:full-service\s+)?branch(?:es| office locations| offices| locations)?)\b", re.I)
ORDINAL_RE = re.compile(rf"\b({'|'.join(ORDINALS)})\s+(?:consecutive\s+)?time\b", re.I)
ORG_RE = re.compile(r"\b(Forbes|Newsweek|BauerFinancial|Bauer|S&P Global|Des Moines Register|Credit Union Journal)\b")
COL_LABEL = {"apy": "APY", "apr": "APR", "rate": "rate", "div": "dividend rate"}


@dataclass
class ConflictRec:
    topic: str
    values: list
    sources: list = field(default_factory=list)   # (url, snapshot_id, quote)
    resolution: Optional[str] = None
    reason: Optional[str] = None
    priority: int = 0
    fact: str = ""          # product-agnostic fact label: business / IRA mirrors of one change share it


def _fmt_values(v: tuple) -> str:
    parts = []
    order = {"apy": 0, "apr": 1, "rate": 2, "div": 3}
    for col, nums in sorted(v, key=lambda x: order.get(x[0].split("-")[0], 9)):
        base = col.split("-")[0]
        label = COL_LABEL.get(base, base) + (" (intro)" if col.endswith("-intro") else "")
        parts.append(" – ".join(f"{n:g}%" for n in nums) + f" {label}")
    return " / ".join(parts)


def _mirror_key(label: str) -> str:
    """'Roth: 10 Month Fixed Special' and '10 Month Fixed Certificate Special' -> '10 month fixed special'."""
    words = re.findall(r"[a-z0-9$.,+-]+", label.lower())
    return " ".join(w for w in words if w not in ("roth:", "roth", "traditional:", "traditional", "business", "ira",
                                                    "certificate", "certificates", "account"))


def _path(url: str) -> str:
    return re.sub(r"^https?://(www\.)?dupaco\.com", "", url) or "/"


class ConflictFinder:
    def __init__(self, store: Store) -> None:
        self.s = store

    def compute(self) -> list[ConflictRec]:
        if not self.s.snapshots:
            return []
        out = self._table_conflicts() + self._count_conflicts() + self._ordinal_conflicts()
        out.sort(key=lambda c: (-c.priority, c.topic))
        # the same rate change mirrored on sibling products (business / IRA copies of a certificate special)
        # is one contradiction, not several
        seen, uniq = set(), []
        for c in out:
            sig = (frozenset(c.values), c.resolution, c.fact or c.topic)
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(c)
        return uniq

    # ------------------------------------------------------------------ table facts
    def _table_conflicts(self) -> list[ConflictRec]:
        s = self.s
        latest = s.snapshot_for(None)
        per_key: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
        for si, groups in s.fact_groups.items():
            for key, cids in groups.items():
                for cid in cids:
                    c = s.chunks[cid]
                    if s.pages[c.url].kind not in RATE_PAGE_KINDS:
                        continue
                    v = row_values(c)
                    if v:
                        per_key[key][v].append((si, cid))
        out = []
        for key, vmap in per_key.items():
            if not any(si == latest for occ in vmap.values() for si, _ in occ):
                continue   # a fact that no longer exists is history, not an open contradiction
            vals = list(vmap)
            if not any(values_disagree(a, b) for i, a in enumerate(vals) for b in vals[i + 1:]):
                continue
            # winner: the value the answer path uses in the newest snapshot that still has this fact
            last_si = max(si for occ in vmap.values() for si, _ in occ)
            cands = [cid for occ in vmap.values() for si, cid in occ
                     if si == last_si and cid not in s.shadowed.get(si, set())]
            if not cands:
                continue
            win = max(cands, key=lambda x: s.claim(x, last_si))
            wv = row_values(s.chunks[win])
            wc = s.chunks[win]
            label = ", ".join(dict.fromkeys(v for k, v in wc.cells.items()
                                            if "%" not in v and not re.search(r"(?i)^min|payment|points", k)))
            product = wc.ctx.split(" > ")[-1] if wc.ctx else ""
            term = "".join(f" {n}-month" for n in re.findall(r"(\d+)\s*Months?", wc.text))
            topic = f"{product}: {label}{term}".strip(": ")
            # how the losing values lost: stale page in the same snapshot, or superseded by a later crawl
            same_snap_stale = any(si == latest and cid in s.shadowed.get(si, set())
                                  for v, occ in vmap.items() if v != wv for si, cid in occ)
            losers = []
            for v, occ in vmap.items():
                if not values_disagree(v, wv):
                    continue
                si, cid = max(occ, key=lambda t: t[0])
                losers.append((v, si, cid))
            sources = [(wc.url, s.snapshots[last_si][0], wc.text[:200])]
            for v, si, cid in losers:
                sources.append((s.chunks[cid].url, s.snapshots[si][0], s.chunks[cid].text[:200]))
            reasons = []
            for v, si, cid in losers:
                lc = s.chunks[cid]
                if si == last_si:
                    reasons.append(f"{_path(lc.url)} still shows {_fmt_values(v)} (effective {lc.effective or 'undated'}, "
                                   f"{s.pages[lc.url].kind} page) - stale copy")
                else:
                    reasons.append(f"{_fmt_values(v)} was on {_path(lc.url)} in {s.snapshots[si][0]} and was superseded")
            reason = (f"{_path(wc.url)} ({s.pages[wc.url].kind}, effective {wc.effective or 'undated'}) in "
                      f"{s.snapshots[last_si][0]} is authoritative and newest; " + "; ".join(reasons))
            values = [_fmt_values(v) for v, _, _ in losers] + [_fmt_values(wv)]
            out.append(ConflictRec(topic=topic, values=values, sources=sources, resolution=_fmt_values(wv),
                                   reason=reason, priority=2 if same_snap_stale else 1, fact=_mirror_key(label)))
        return out

    # ------------------------------------------------------------------ text facts
    def _dated_paragraphs(self):
        s = self.s
        latest = s.snapshot_for(None)
        bit = 1 << latest
        for cid, c in enumerate(s.chunks):
            if c.kind != "para" or not s.chunk_mask[cid] & bit:
                continue
            info = s.pages[c.url]
            if info.kind in ("index", "junk"):
                continue
            yield cid, c, info

    def _count_conflicts(self) -> list[ConflictRec]:
        s = self.s
        sid = s.snapshots[s.snapshot_for(None)][0]
        stmts = []
        for cid, c, info in self._dated_paragraphs():
            for m in COUNT_RE.finditer(c.text):
                n = int(m.group(1))
                window = c.text[max(0, m.start() - 80): m.end() + 40]
                if n < 5 or re.search(r"nationwide|across the u|participating|shared branch|co-op", window, re.I):
                    continue
                if not re.search(r"dupaco|\bour\b|\bit has\b|\boperates\b", window, re.I):
                    continue
                stmts.append((info.pub_date, n, m.group(0), cid))
        dated = [x for x in stmts if x[0]]
        if len({n for _, n, _, _ in dated}) < 2:
            return []
        newest = max(dated, key=lambda x: (x[0], x[1]))
        by_val: dict[int, tuple] = {}
        for pub, n, phrase, cid in sorted(dated, key=lambda x: x[0]):
            by_val[n] = (pub, phrase, cid)
        values = [f"{n} branches" for n in sorted(by_val) if n != newest[1]] + [f"{newest[1]} branches"]
        sources = [(s.chunks[cid].url, sid, phrase) for n, (pub, phrase, cid) in sorted(by_val.items())]
        olds = ", ".join(f"{n} ({pub.isoformat()})" for n, (pub, _, _) in sorted(by_val.items()) if n != newest[1])
        return [ConflictRec(
            topic="Number of Dupaco branches (branch office locations count)", values=values, sources=sources,
            resolution=f"{newest[1]} branches",
            reason=f"most recent statement ({_path(s.chunks[newest[3]].url)}, published {newest[0].isoformat()}) "
                   f"says {newest[1]}; older press releases still say {olds} (e.g. before the Hy-Vee branch closed)",
            priority=2)]

    def _ordinal_conflicts(self) -> list[ConflictRec]:
        s = self.s
        sid = s.snapshots[s.snapshot_for(None)][0]
        per_org: dict[str, list] = defaultdict(list)
        for cid, c, info in self._dated_paragraphs():
            if not info.pub_date:
                continue
            for sent in re.split(r"(?<=[.!?])\s+", c.text):
                m = ORDINAL_RE.search(sent)
                org = ORG_RE.search(sent)
                if m and org:
                    per_org[org.group(1)].append((info.pub_date, ORDINALS.index(m.group(1).lower()), sent, cid))
        out = []
        for org, xs in per_org.items():
            if len({o for _, o, _, _ in xs}) < 2:
                continue
            newest = max(xs, key=lambda x: (x[0], x[1]))
            vals = sorted({o for _, o, _, _ in xs})
            values = [f"{ORDINALS[o]} time" for o in vals if o != newest[1]] + [f"{ORDINALS[newest[1]]} time"]
            sources = [(s.chunks[cid].url, sid, sent[:200]) for _, _, sent, cid in sorted(xs, key=lambda x: x[0])]
            out.append(ConflictRec(
                topic=f"How many times {org} has recognized Dupaco", values=values, sources=sources,
                resolution=f"{ORDINALS[newest[1]]} time",
                reason=f"the newest {org} release ({newest[0].isoformat()}) says it was the "
                       f"{ORDINALS[newest[1]]} time; older releases stay online with earlier counts",
                priority=2))
        return out
