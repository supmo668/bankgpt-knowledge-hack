"""Versioned fact store: every chunk of every snapshot, deduplicated, with a BM25 inverted index.

A chunk is identified by (url, context, text). Each chunk carries a bitmask of the snapshots it
appeared in, so a newer snapshot never overwrites an older one.

Time model (v2, see NOTES.md "Validity intervals"): the store answers from *views*. Every crawl is a view
dated on its `taken_on`. Between two crawls, a table whose next crawl states an effective date inside the
gap, or an offer whose end date falls inside it, opens an *interval view* on that date: the earlier crawl
with the newer table version swapped in (and ended offers closed). `view_for(as_of)` picks the newest view
dated on or before `as_of`, so an `as_of` between crawls lands on the stated effective date. On top of the
views, every fact key has a timeline of versions with `valid_from` / `valid_to` (`Store.timeline`).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
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
# words that name no particular branch: "Branch Hours", "Locations", "Lobby and drive-up hours", a "Day" header
GENERIC_SCOPE_RE = re.compile(r"(?i)\b(?:branch(?:es)?|offices?|locations?|centers?|hours|lobby|drive[- ]?(?:up|thru|through)|"
                              r"our|and|days?|find)\b|&")
HOURS_KEY = "hours | "


def _scope_name(heading: str) -> str:
    return re.sub(r"\s+", " ", GENERIC_SCOPE_RE.sub(" ", heading)).strip(" :-,").lower()


def _branch_scope(c: Chunk) -> str:
    """Which branch an hours row belongs to: the nearest heading naming a branch ("Main Street Branch"), else the
    nearest specific heading ("## Downtown" on an index page). Generic words are dropped, so "Main Street Branch"
    and "Main Street" are one branch, and a "## Branch Hours" heading names none."""
    parts = [x.strip() for x in (c.ctx or "").split(" > ") if x.strip()]
    names = [(x, _scope_name(x)) for x in parts]
    named = [n for x, n in names if n and BRANCH_SCOPE_RE.search(x)]
    specific = [n for _, n in names if n]
    return (named or specific or [re.sub(r"^https?://[^/]+", "", c.url).lower()])[-1]


def _hours_col(c: Chunk, header: str) -> str:
    """The service an hours column covers: lobby or drive-up, from the header or else the table's heading/caption."""
    for text in (header, c.ctx or ""):
        drive, lobby = re.search(r"(?i)drive", text), re.search(r"(?i)lobby", text)
        if bool(drive) != bool(lobby):
            return "drive-up" if drive else "lobby"
    return header.lower()


def hours_key_parts(key: str):
    """('main street', 'monday-friday') for an hours fact key, else None."""
    if not key.startswith(HOURS_KEY):
        return None
    branch, _, day = key[len(HOURS_KEY):].partition(" | ")
    return branch, day


# caption words that make a different product of otherwise identical row labels
CAPTION_QUALIFIERS = re.compile(r"\b(cash-out|home equity|jumbo|construction|interest-only|bridge)\b")
FAMILY_RE = re.compile(r"(?i)\b(atv|snowmobile|jet ski|boat|rv|recreational|motorcycle loans? only|auto|vehicle)")


def row_key(c: Chunk) -> Optional[str]:
    """Fact key of a table row = its qualifier cells (term, tier, product), not its values."""
    if c.kind != "row" or not c.cells:
        return None
    if hours_cells(c):   # hours facts: one per (branch, day); the times are the values, not qualifiers
        day = re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", next(iter(c.cells.values())).lower().replace("–", "-")))
        return f"{HOURS_KEY}{_branch_scope(c)} | {day.strip()}"
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
    else:
        # a self-describing row ("Refinance: 30-Year Fixed Conventional $300,000 or less") still belongs to its
        # table's product: Home Loan, Cash-Out and Home Equity Refinance tables repeat the same row labels
        cap = c.ctx.split(" > ")[-1].lower() if c.ctx else ""
        quals = [w for w in CAPTION_QUALIFIERS.findall(cap) if w not in q]
        if quals:
            q = f"{' '.join(dict.fromkeys(quals))} | {q}"
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
        return tuple(sorted((f"hours:{_hours_col(c, k)}", (v,)) for k, v in hours.items()))
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


def _fmt_vals(v: tuple) -> str:
    return ", ".join(f"{col} " + "/".join(f"{n:g}" if isinstance(n, float) else str(n) for n in nums)
                     for col, nums in v)


@dataclass
class View:
    """One state of the site: a crawl (`snap`), or an interval view between crawl `snap` and crawl `nxt`."""
    date: str                  # ISO date the view starts
    snap: int                  # snapshot index the view is based on
    nxt: Optional[int] = None  # interval views: the later crawl whose dated tables were swapped in
    swapped: tuple = ()        # table keys taken from the later crawl
    why: str = ""              # what opened the view ("effective 09/25/2026 on /rates")
    trailing: bool = False     # a view after the newest crawl, opened by an offer's stated end date


@dataclass
class FactVersion:
    """One value of one fact key, with the interval it was the resolved (current) value."""
    key: str
    value: tuple
    valid_from: str            # ISO date
    valid_to: Optional[str]    # ISO date the next version (or an end date) took over; None = still current
    url: str
    snapshot_id: str           # crawl the value was read from
    effective: Optional[str]   # the date the page states, verbatim
    closed_reason: str = ""    # why the version closed ("replaced by ... effective ...", "offer ended ...")
    shadowed: bool = False     # True for a losing copy (stale page) shown while another version was current


def _iso(d: date) -> str:
    return d.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


class Store:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, str]] = []          # (snapshot_id, taken_on), ingest order
        self.views: list[View] = []                          # view id -> View (bit position in chunk_view)
        self.snap_view: list[int] = []                       # snapshot index -> its crawl view id
        self.chunks: list[Chunk] = []
        self.chunk_mask: list[int] = []                      # snapshots whose crawl contained the chunk
        self.chunk_view: list[int] = []                      # bitmask of view ids where the chunk is visible
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
        self.shadowed: dict[int, set] = {}                   # view -> row chunks that lost their fact
        self.fact_groups: dict[int, dict] = {}               # view -> fact key -> [cid]
        self.winners: dict[int, dict] = {}                   # view -> fact key -> winning cid
        self._timelines: Optional[dict] = None
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
        # the crawl view = the newest crawled version of every page seen so far (a page missing from one
        # crawl is not deleted; it is carried forward, with a penalty at ranking time)
        prev_view = self.snap_view[-1] if self.snap_view else None
        vid = self._new_view(View(date=taken_on, snap=si))
        self.snap_view.append(vid)
        vbit = 1 << vid
        for cid, c in enumerate(self.chunks):
            if self.chunk_mask[cid] >> self.url_version[c.url] & 1:
                self.chunk_view[cid] |= vbit
        self._resolve_facts(vid)
        if prev_view is not None:
            self._interval_views(prev_view, vid)
        self._trailing_views(vid)
        self._timelines = None
        self._finalize()
        return si

    # ------------------------------------------------------------------ views
    def _new_view(self, v: View) -> int:
        self.views.append(v)
        return len(self.views) - 1

    def view_date(self, v: int) -> str:
        return self.views[v].date

    def view_sid(self, v: int, cid: Optional[int] = None) -> str:
        """Snapshot id to cite for view `v` (for a swapped-in row of an interval view: the later crawl)."""
        vw = self.views[v]
        if cid is not None and vw.nxt is not None and not self.chunk_mask[cid] >> vw.snap & 1 \
                and self.chunk_mask[cid] >> vw.nxt & 1:
            return self.snapshots[vw.nxt][0]
        return self.snapshots[vw.snap][0]

    def crawl_views(self) -> list[int]:
        return list(self.snap_view)

    def _table_versions(self, vid: int) -> dict[str, frozenset]:
        bit = 1 << vid
        out: dict[str, set] = defaultdict(set)
        for t, cids in self.table_rows.items():
            for cid in cids:
                if self.chunk_view[cid] & bit:
                    out[t].add(cid)
        return {t: frozenset(x) for t, x in out.items()}

    def _interval_views(self, va: int, vb: int) -> None:
        """Interval views between crawl views `va` (older) and `vb`: one per date inside the gap on which a
        table of the later crawl states it took effect, or an offer visible in the earlier crawl ended."""
        a, b = self.views[va], self.views[vb]
        da, db = date.fromisoformat(a.date), date.fromisoformat(b.date)
        ta, tb = self._table_versions(va), self._table_versions(vb)
        swaps: list[tuple[date, str, str]] = []
        for t, rows in tb.items():
            old = ta.get(t)
            if old == rows:
                continue
            effs = [self.chunks[c].effective for c in rows if self.chunks[c].effective]
            if not effs:
                continue   # undated new version: valid from the crawl that first shows it
            eff = max(effs, key=_eff_date)
            e = _eff_date(eff)
            if da < e < db:
                swaps.append((e, t, eff))
            # e <= da: the earlier crawl still showed the old version on a date the new one claims to cover;
            # the observation wins and the switch stays at the later crawl (see NOTES.md)
        ends: list[tuple[date, str]] = []
        abit = 1 << va
        for cid, c in enumerate(self.chunks):
            if c.ends and self.chunk_view[cid] & abit:
                end = _parse_iso(c.ends)
                if end is None:
                    continue
                nxt = end + timedelta(days=1)
                if da < nxt < db:
                    ends.append((nxt, c.url))
        dates = sorted({d for d, _, _ in swaps} | {d for d, _ in ends})
        for d in dates:
            swap_now = sorted(t for e, t, _ in swaps if e <= d)
            why = "; ".join(dict.fromkeys(
                [f"effective {eff} on {_short_path(t.split('#')[0])}" for e, t, eff in swaps if e == d] +
                [f"offer on {_short_path(u)} ended {(d - timedelta(days=1)).isoformat()}" for e, u in ends if e == d]))
            vid = self._new_view(View(date=_iso(d), snap=a.snap, nxt=b.snap, swapped=tuple(swap_now), why=why))
            bit = 1 << vid
            drop = set().union(*(ta.get(t, frozenset()) for t in swap_now)) if swap_now else set()
            add = set().union(*(tb[t] for t in swap_now)) if swap_now else set()
            for cid in range(len(self.chunks)):
                if (self.chunk_view[cid] & abit and cid not in drop) or cid in add:
                    self.chunk_view[cid] |= bit
            self._resolve_facts(vid)

    def _trailing_views(self, vb: int) -> None:
        """Views after the newest crawl: one the day after each stated end date of an offer that crawl still
        shows, so an `as_of` past the end no longer quotes the offer. Only the newest crawl's are used."""
        b = self.views[vb]
        bbit = 1 << vb
        ends = set()
        for cid, c in enumerate(self.chunks):
            if c.ends and self.chunk_view[cid] & bbit:
                end = _parse_iso(c.ends)
                if end and _iso(end) >= b.date:
                    ends.add((end + timedelta(days=1), c.url))
        for d in sorted({d for d, _ in ends}):
            why = "; ".join(dict.fromkeys(f"offer on {_short_path(u)} ended {(d - timedelta(days=1)).isoformat()}"
                                          for e, u in sorted(ends) if e == d))
            vid = self._new_view(View(date=_iso(d), snap=b.snap, why=why, trailing=True))
            bit = 1 << vid
            for cid in range(len(self.chunks)):
                if self.chunk_view[cid] & bbit:
                    self.chunk_view[cid] |= bit
            self._resolve_facts(vid)

    def live_views(self) -> list[int]:
        """Every view still in force: crawls, interval views, and the newest crawl's trailing views."""
        latest = self.views[self.snapshot_for(None)].snap if self.snapshots else -1
        return [v for v, vw in enumerate(self.views) if not vw.trailing or vw.snap == latest]

    def claim(self, cid: int, si: int, effective: Optional[str] = None) -> tuple:
        """How strongly a row (or a paragraph, with the date its sentence states) claims to be current in view
        `si`: offer live, effective date, page present, authority, publication date, then recency."""
        c = self.chunks[cid]
        info = self.pages[c.url]
        present = self.present(c.url, si)
        live = not (c.ends and c.ends < self.view_date(si))
        mask = self.chunk_mask[cid]
        first_seen = (mask & -mask).bit_length()      # snapshot this exact value first appeared in
        return (live, _eff_date(effective or c.effective), present, info.authority, info.pub_date or date.max,
                first_seen, cid)

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
        """The page was crawled for view `si` (its base crawl, or the later crawl of an interval view)."""
        v = self.views[si]
        m = self.page_snaps.get(url, 0)
        return bool(m >> v.snap & 1) or (v.nxt is not None and bool(m >> v.nxt & 1)
                                         and any(t.split("#")[0] == url for t in v.swapped))

    def expired(self, cid: int, si: int) -> bool:
        """An offer whose stated end date is before the view date."""
        e = self.chunks[cid].ends
        return bool(e) and e < self.view_date(si)

    def _resolve_facts(self, si: int) -> None:
        bit = 1 << si
        groups: dict[str, list[int]] = defaultdict(list)
        for cid, c in enumerate(self.chunks):
            if c.kind == "row" and self.chunk_view[cid] & bit:
                k = row_key(c)
                if k and row_values(c):
                    groups[k].append(cid)
        shadow = set()
        winners = {}
        for k, cids in groups.items():
            win = max(cids, key=lambda x: self.claim(x, si))
            winners[k] = win
            if len({row_values(self.chunks[x]) for x in cids}) < 2:
                continue
            wv = row_values(self.chunks[win])
            shadow.update(x for x in cids if values_disagree(row_values(self.chunks[x]), wv))
        self.shadowed[si] = shadow
        self.fact_groups[si] = dict(groups)
        self.winners[si] = winners

    def _finalize(self) -> None:
        n = len(self.chunks) or 1
        self._avglen = sum(self.chunk_len) / n
        self._idf = {t: math.log(1 + (n - len(ps) + 0.5) / (len(ps) + 0.5)) for t, ps in self.postings.items()}

    # ------------------------------------------------------------------ query
    def snapshot_for(self, as_of: Optional[str]) -> int:
        """Crawl view of the newest snapshot taken on or before `as_of` (the oldest if as_of predates all)."""
        if not self.snapshots:
            return -1
        if not as_of:
            return self.snap_view[max(range(len(self.snapshots)), key=lambda i: (self.snapshots[i][1], i))]
        best = None
        for i, (_, taken) in enumerate(self.snapshots):
            if taken <= as_of[:10] and (best is None or taken >= self.snapshots[best][1]):
                best = i
        if best is None:
            best = min(range(len(self.snapshots)), key=lambda i: self.snapshots[i][1])
        return self.snap_view[best]

    def view_for(self, as_of: Optional[str]) -> int:
        """The view in force on `as_of`: the newest crawl or interval view dated on or before it. `None` = the
        latest crawl; a date before the first crawl = the first crawl."""
        base = self.snapshot_for(as_of)
        if base < 0 or not as_of:
            return base
        d = as_of[:10]
        best = base
        latest = base == self.snapshot_for(None)
        for vid, v in enumerate(self.views):
            if (v.nxt is not None or (v.trailing and latest)) and v.snap == self.views[base].snap \
                    and v.date <= d and v.date >= self.views[best].date:
                best = vid
        return best

    # ------------------------------------------------------------------ fact timelines
    def timeline(self, key: str) -> list[FactVersion]:
        """Every version of one fact key, oldest first: the resolved value per interval, plus the shadowed
        copies (stale pages) that were visible but lost, each closed with the rule that beat it."""
        return self.timelines().get(key, [])

    def timelines(self) -> dict[str, list[FactVersion]]:
        if self._timelines is None:
            self._timelines = self._build_timelines()
        return self._timelines

    def facts_at(self, as_of: Optional[str]) -> dict[str, FactVersion]:
        """The current version of every fact key on `as_of` (the interval that contains it)."""
        d = (as_of or self.view_date(self.snapshot_for(None)))[:10]
        out = {}
        for k, vs in self.timelines().items():
            for fv in vs:
                if not fv.shadowed and fv.valid_from <= d and (fv.valid_to is None or d < fv.valid_to):
                    out[k] = fv
        return out

    def _build_timelines(self) -> dict[str, list[FactVersion]]:
        order = sorted(self.live_views(), key=lambda v: (self.views[v].date, v))
        pos = {v: i for i, v in enumerate(order)}
        crawl_date = self.view_date(self.snapshot_for(None)) if order else ""   # newest observation
        keys = set()
        for v in order:
            keys.update(self.winners.get(v, {}))
        out: dict[str, list[FactVersion]] = {}
        for k in keys:
            # runs over every view in date order: [value, first view, last view, winning cid]; value None = the
            # key is not on the site in that view (also before it first appears, so a backdated claim can't
            # reach over a crawl that did not show it)
            runs: list[list] = []
            for v in order:
                win = self.winners.get(v, {}).get(k)
                val = row_values(self.chunks[win]) if win is not None else None
                if runs and runs[-1][0] == val:
                    runs[-1][2] = v
                else:
                    runs.append([val, v, v, win])
            versions: list[FactVersion] = []
            for i, (val, first, last, win) in enumerate(runs):
                if val is None:
                    continue
                c = self.chunks[win]
                start = self.view_date(first)
                eff = _eff_date(c.effective)
                if i == 0 and eff != date.min and _iso(eff) < start:
                    start = _iso(eff)   # stated effective date, when no earlier view contradicts it
                nxt = runs[i + 1] if i + 1 < len(runs) else None
                end, reason = None, ""
                if nxt is not None:
                    end = self.view_date(nxt[1])
                    if nxt[0] is None:
                        reason = "no longer on the site"
                    else:
                        nc = self.chunks[nxt[3]]
                        reason = (f"replaced by {_fmt_vals(nxt[0])} on {_short_path(nc.url)}"
                                  + (f" (effective {nc.effective})" if nc.effective else ""))
                ends = _parse_iso(c.ends)
                if ends is not None:
                    stop = _iso(ends + timedelta(days=1))
                    if stop <= start:
                        continue    # the offer had already ended when this value was the only one shown
                    if end is None or stop < end:
                        end = stop
                        reason = f"offer ended {c.ends}" if stop <= crawl_date else f"offer ends {c.ends} (stated end date)"
                versions.append(FactVersion(k, val, start, end, c.url, self.view_sid(first, win), c.effective,
                                            reason))
            # losing copies that were visible while another version was current: stored, closed, with the reason;
            # one version per contiguous stretch of views in which the copy lost
            spans: list[list] = []           # [cid, first view, last view, winner at last view]
            open_span: dict[int, list] = {}
            for v in order:
                win = self.winners.get(v, {}).get(k)
                sh = self.shadowed.get(v, set())
                for cid in self.fact_groups.get(v, {}).get(k, []):
                    if cid not in sh:
                        continue
                    sp = open_span.get(cid)
                    if sp is not None and pos[sp[2]] == pos[v] - 1:
                        sp[2:] = [v, win]
                    else:
                        sp = [cid, v, v, win]
                        open_span[cid] = sp
                        spans.append(sp)
            for cid, first, last, win in sorted(spans, key=lambda sp: (self.view_date(sp[1]), sp[0], pos[sp[1]])):
                lc = self.chunks[cid]
                after = order[pos[last] + 1] if pos[last] + 1 < len(order) else None
                versions.append(FactVersion(k, row_values(lc), self.view_date(first),
                                            self.view_date(after) if after is not None else None, lc.url,
                                            self.view_sid(first, cid), lc.effective,
                                            f"shadowed: loses to {_short_path(self.chunks[win].url)} on "
                                            f"{self.why_wins(win, cid, last)}", shadowed=True))
            out[k] = versions
        return out

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
