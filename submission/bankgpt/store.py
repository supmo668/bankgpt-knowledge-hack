"""Versioned fact store: every chunk of every snapshot, deduplicated, with a BM25 inverted index.

A chunk is identified by (url, context, text). Each chunk carries a bitmask of the snapshots it
appeared in, so `as_of` selection is a bit test and a newer snapshot never overwrites an older one.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from typing import Optional

from .chunker import Chunk, PageInfo, chunk_page, hours_cells, page_info
from .text import tokens

VALUE_CELL_RE = re.compile(r"\d(?:\.\d+)?\s?%|\$\s?\d|^\s*[\d.,]+\s*$")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _eff_date(s: Optional[str]) -> date:
    if not s:
        return date.min
    try:
        m, d, y = (int(x) for x in s.split("/"))
        return date(y + 2000 if y < 100 else y, m, d)
    except ValueError:
        return date.min


# Branch hours (fact family added for R8): see chunker.hours_cells.
BRANCH_SCOPE_RE = re.compile(r"(?i)\b(branch|office|location|center)\b")


def _branch_scope(c: Chunk) -> str:
    """Which branch an hours row belongs to: the nearest heading naming a branch, else the page's first heading."""
    parts = [x.strip() for x in (c.ctx or "").split(" > ") if x.strip()]
    named = [x for x in parts if BRANCH_SCOPE_RE.search(x)]
    scope = named[-1] if named else (parts[0] if parts else re.sub(r"^https?://[^/]+", "", c.url))
    return re.sub(r"\s+", " ", re.sub(r"(?i)\bhours\b.*$", "", scope)).strip(" :").lower()


FAMILY_RE = re.compile(r"(?i)\b(atv|snowmobile|jet ski|boat|rv|recreational|motorcycle loans? only|auto|vehicle)")


def row_key(c: Chunk) -> Optional[str]:
    """Fact key of a table row = its qualifier cells (term, tier, product), not its values."""
    if c.kind != "row" or not c.cells:
        return None
    if hours_cells(c):   # hours facts: one per (branch, day); the times are the values, not qualifiers
        day = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", next(iter(c.cells.values())).lower().replace("–", "-")))
        return f"hours | {_branch_scope(c)} | {day.strip()}"
    quals = [c.key_alias.get(v, v) for k, v in c.cells.items()
             if not re.search(r"(?i)^min|minimum|payment", k) and v.strip().lower() not in ("none", "n/a", "na", "—", "-") and not VALUE_CELL_RE.search(v) or re.search(r"(?i)month|year|tier|\+|or less|–|-", v)
             and not re.search(r"%", v)]
    if not quals:
        return None
    q = " | ".join(quals).lower()
    q = re.sub(r"(\d)[\s,](?=\d{3}\b)", r"\1", q.replace("–", "-"))
    q = re.sub(r"\s*-\s*", "-", re.sub(r"[\s,]+", " ", q)).strip()
    first = next(iter(c.cells.values()), "")
    if len(re.findall(r"[a-z]{3,}", q)) < 3 or not re.search(r"\d", first):
        # a generic row label ("Variable Interest Rate", "60 Month") needs its table's product name
        cap = c.ctx.split(" > ")[-1].lower() if c.ctx else ""
        # "Regular" vs "Regular Certificates": captions drift between crawls; keep only the distinctive words
        cap = " ".join(w for w in re.findall(r"[a-z0-9$&']+", cap)
                       if w not in ("certificates", "certificate", "accounts", "account", "rates", "rate", "loans"))
        if cap and cap not in q:
            q = f"{cap} | {q}"
    scope = "business" if "business" in c.url.lower() else "personal"
    if c.key_alias:
        fam = FAMILY_RE.search(f"{c.ctx} {c.url}")
        fam_name = fam.group(1).lower() if fam else "other"
        fam_name = {"vehicle": "auto", "motorcycle loans only": "auto", "motorcycle loan only": "auto",
                    "rv": "boat", "jet ski": "atv", "snowmobile": "atv"}.get(fam_name, fam_name)
        scope += f" | {fam_name}"
    return f"{scope} | {q}"


def _canon_col(header: str, cell: str) -> str:
    h, v = header.lower(), cell.lower()
    if "apr" in v or "apr" in h or "annual percentage rate" in h:
        col = "apr"
    elif "apy" in v or "apy" in h or "yield" in h:
        col = "apy"
    elif "dividend" in h:
        col = "div"
    else:
        col = "rate"
    if re.search(r"1st|first|intro|promo", h):
        col += "-intro"
    return col


def row_values(c: Chunk) -> tuple:
    """Value cells as (canonical column, numbers), so 6.000% == 6.00% and APR == Annual Percentage Rate."""
    hours = hours_cells(c)
    if hours:
        return tuple(sorted((f"hours:{k.lower()}", (v,)) for k, v in hours.items()))
    vals = []
    for k, v in c.cells.items():
        if re.search(r"(?i)payment|min|points|tier|balance|amount|limit|example", k):
            continue
        is_fee = "$" in v and (k in ("Value",) or re.search(r"(?i)fee|cost|price|charge", k)) and not re.search(r"[–+-]|or less", v)
        if "%" not in v and not is_fee:
            continue
        nums = tuple(round(float(x.replace(",", "")), 4) for x in NUM_RE.findall(v))
        if nums:
            vals.append((_canon_col(k, v), nums))
    return tuple(sorted(vals))


K1, B = 1.2, 0.5


def values_disagree(a: tuple, b: tuple) -> bool:
    da, db = dict(a), dict(b)
    shared = set(da) & set(db)
    return any(da[k] != db[k] for k in shared)


def _short_path(url: str) -> str:
    return re.sub(r"^https?://[^/]+", "", url) or "/"


class Store:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, str]] = []          # (snapshot_id, taken_on), ingest order
        self.chunks: list[Chunk] = []
        self.chunk_mask: list[int] = []                      # snapshots whose crawl contained the chunk
        self.chunk_view: list[int] = []                      # views (as_of = snapshot i) where it is visible
        self.url_version: dict[str, int] = {}                # url -> newest snapshot that crawled it
        self.chunk_len: list[int] = []
        self.chunk_tf: list[dict] = []
        self.key2id: dict[tuple, int] = {}
        self.pages: dict[str, PageInfo] = {}                 # url -> latest PageInfo
        self.page_snaps: dict[str, int] = defaultdict(int)   # url -> snapshot mask
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.table_rows: dict[str, list[int]] = defaultdict(list)
        self.url_chunks: dict[str, list[int]] = defaultdict(list)
        self._idf: dict[str, float] = {}
        self.shadowed: dict[int, set] = {}                   # snapshot -> row chunks that lost their fact
        self.fact_groups: dict[int, dict] = {}               # snapshot -> fact key -> [cid]
        self._avglen = 1.0

    # ------------------------------------------------------------------ ingest
    def add_snapshot(self, snapshot_id: str, taken_on: str, pages) -> int:
        si = len(self.snapshots)
        self.snapshots.append((snapshot_id, taken_on))
        bit = 1 << si
        for p in pages:
            info = page_info(p.url, p.title, p.markdown, p.fetched_at)
            self.pages[p.url] = info
            self.page_snaps[p.url] |= bit
            for c in chunk_page(p.url, info.title, p.markdown):
                # per-crawl attributes are part of the identity: an unchanged row whose table's effective
                # date moved (or whose model-year rank / table changed) is a new version, not the old one
                key = (c.url, c.ctx, c.text, c.table, c.effective, c.ends, tuple(sorted(c.key_alias.items())))
                cid = self.key2id.get(key)
                if cid is None:
                    cid = len(self.chunks)
                    self.key2id[key] = cid
                    self.chunks.append(c)
                    self.chunk_mask.append(0)
                    self.chunk_view.append(0)
                    toks = tokens(f"{info.title} {c.ctx} {c.text}")
                    tf: dict[str, int] = defaultdict(int)
                    for t in toks:
                        tf[t] += 1
                    self.chunk_tf.append(dict(tf))
                    self.chunk_len.append(len(toks))
                    for t in tf:
                        self.postings[t].append(cid)
                    if c.table:
                        self.table_rows[c.table].append(cid)
                    self.url_chunks[c.url].append(cid)
                self.chunk_mask[cid] |= bit
            self.url_version[p.url] = si
        # the view at snapshot i = the newest crawled version of every page seen so far (a page missing from
        # one crawl is not deleted; it is carried forward, with a penalty at ranking time)
        for cid, c in enumerate(self.chunks):
            if self.chunk_mask[cid] >> self.url_version[c.url] & 1:
                self.chunk_view[cid] |= bit
        self._resolve_facts(si)
        self._finalize()
        return si

    def claim(self, cid: int, si: int) -> tuple:
        """How strongly a row claims to be current: effective date, then authority, then recency."""
        c = self.chunks[cid]
        info = self.pages[c.url]
        present = bool(self.page_snaps[c.url] >> si & 1)
        live = not (c.ends and c.ends < self.snapshots[si][1])
        mask = self.chunk_mask[cid]
        first_seen = (mask & -mask).bit_length()      # snapshot this exact value first appeared in
        return (live, _eff_date(c.effective), present, info.authority, info.pub_date or date.max, first_seen, cid)

    def why_wins(self, win: int, lose: int, si: int) -> str:
        """The currency rule that decided between two rows of one fact, in words."""
        a, b = self.claim(win, si), self.claim(lose, si)
        wc, lc = self.chunks[win], self.chunks[lose]
        if a[0] != b[0]:
            return f"the offer on {_short_path(lc.url)} has ended"
        if a[1] != b[1]:
            return (f"newer effective date ({wc.effective or 'undated'} vs {lc.effective or 'undated'} "
                    f"on {_short_path(lc.url)})")
        if a[2] != b[2]:
            return f"{_short_path(lc.url)} is missing from this crawl (carried forward)"
        if a[3] != b[3]:
            return (f"higher-authority source ({self.pages[wc.url].kind} vs {self.pages[lc.url].kind} "
                    f"{_short_path(lc.url)})")
        if a[4] != b[4]:
            return f"more recently published than {_short_path(lc.url)}"
        return f"the newer value (first seen in a later crawl than {_short_path(lc.url)})"

    def present(self, url: str, si: int) -> bool:
        return bool(self.page_snaps.get(url, 0) >> si & 1)

    def expired(self, cid: int, si: int) -> bool:
        """An offer whose stated end date is before the snapshot date."""
        e = self.chunks[cid].ends
        return bool(e) and e < self.snapshots[si][1]

    def _resolve_facts(self, si: int) -> None:
        bit = 1 << si
        groups: dict[str, list[int]] = defaultdict(list)
        for cid, c in enumerate(self.chunks):
            if c.kind == "row" and self.chunk_view[cid] & bit:
                k = row_key(c)
                if k and row_values(c):
                    groups[k].append(cid)
        shadow = set()
        for k, cids in groups.items():
            if len({row_values(self.chunks[x]) for x in cids}) < 2:
                continue
            win = max(cids, key=lambda x: self.claim(x, si))
            wv = row_values(self.chunks[win])
            shadow.update(x for x in cids if values_disagree(row_values(self.chunks[x]), wv))
        self.shadowed[si] = shadow
        self.fact_groups[si] = dict(groups)

    def _finalize(self) -> None:
        n = len(self.chunks) or 1
        self._avglen = sum(self.chunk_len) / n
        self._idf = {t: math.log(1 + (n - len(ps) + 0.5) / (len(ps) + 0.5)) for t, ps in self.postings.items()}

    # ------------------------------------------------------------------ query
    def snapshot_for(self, as_of: Optional[str]) -> int:
        """Newest snapshot taken on or before `as_of` (the oldest one if as_of predates all)."""
        if not self.snapshots:
            return -1
        if not as_of:
            return max(range(len(self.snapshots)), key=lambda i: self.snapshots[i][1])
        best = None
        for i, (_, taken) in enumerate(self.snapshots):
            if taken <= as_of[:10] and (best is None or taken > self.snapshots[best][1]):
                best = i
        return best if best is not None else min(range(len(self.snapshots)), key=lambda i: self.snapshots[i][1])

    def idf(self, t: str) -> float:
        return self._idf.get(t, math.log(1 + len(self.chunks) + 0.5))

    def bm25(self, qtoks: list[str], si: int) -> dict[int, float]:
        bit = 1 << si
        scores: dict[int, float] = defaultdict(float)
        for t in dict.fromkeys(qtoks):
            ps = self.postings.get(t)
            if not ps:
                continue
            idf = self._idf[t]
            for cid in ps:
                if not self.chunk_view[cid] & bit:
                    continue
                f = self.chunk_tf[cid][t]
                dl = self.chunk_len[cid]
                scores[cid] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self._avglen))
        return scores

    def page_chunks(self, url: str, si: int) -> list[int]:
        bit = 1 << si
        return [cid for cid in self.url_chunks.get(url, []) if self.chunk_view[cid] & bit]

    def rows_of(self, table: str, si: int) -> list[int]:
        bit = 1 << si
        sh = self.shadowed.get(si, set())
        return [cid for cid in self.table_rows.get(table, []) if self.chunk_view[cid] & bit and cid not in sh]

    def recency(self, info: PageInfo, ref: date) -> float:
        """1.0 for undated live pages; dated pages decay over ~6 years before the snapshot date."""
        if info.pub_date is None:
            return 1.0
        age = max(0, (ref - info.pub_date).days)
        return max(0.0, 1.0 - age / (365 * 6))
