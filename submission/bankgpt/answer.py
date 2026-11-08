"""Question -> one grounded answer. Rerank BM25 hits by authority, recency and qualifiers;
refuse when the site does not say; compose a short answer with one value per fact."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .chunker import Chunk
from .store import Store
from .text import amount_range, expand_query, money_values, tokens, year_range

REFUSAL_TEXT = "I don't know: that isn't listed on the website. Please contact us and a team member can help."

RATE_INTENT_RE = re.compile(r"\b(rates?|aprs?|apys?|dividends?|yield|interest)\b", re.I)
FEE_INTENT_RE = re.compile(r"\b(fees?|cost|costs|charge|price|how much)\b", re.I)
HOURS_INTENT_RE = re.compile(r"\bhours\b|\bwhat time\b|\bopen (on|until)\b", re.I)
TIME_RE = re.compile(r"\b\d{1,2}(:\d\d)?\s?(a\.?m\.?|p\.?m\.?)|\bnoon\b", re.I)
LIST_INTENT_RE = re.compile(r"\b(what|which) (\w+ ){0,3}(do|does) (you|dupaco) (offer|have)\b|\blist\b|\ball (of )?(the|your)\b|"
                            r"\b(rates|aprs|apys) (for|on) (the|your)\b|\bwhat are (the|your|their) (\w+ ){0,3}(rates|aprs|apys)\b|"
                            r"\b(\w+ )?(rates|aprs|apys)\b.*\b(right now|today)\b|\bprovide (\w+ ){0,3}rates\b", re.I)
HAS_RATE_RE = re.compile(r"\d+(?:\.\d+)?\s?%\s*(?:apr|apy|annual|fixed|variable|rate|dividend)|"
                         r"(?:rate|apr|apy|yield|dividend)\w*[^.;]{0,40}?\d+(?:\.\d+)?\s?%", re.I)
COUNT_INTENT_RE = re.compile(r"\bhow many\b", re.I)
LINK_INTENT_RE = re.compile(r"\b(link|url|website address|web address|web page|webpage)\b", re.I)
SUPER_HI_RE = re.compile(r"\b(highest|best|top|maximum|max|most)\b", re.I)
SUPER_LO_RE = re.compile(r"\b(lowest|minimum|cheapest|least)\b", re.I)
TERM_RE = re.compile(r"\b(\d{1,3})[\s-]*(month|mo|year|yr)s?\b", re.I)
MODEL_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
GENERIC_CAPS = {"what", "how", "when", "where", "which", "who", "why", "is", "are", "do", "does", "can", "could",
                "would", "i", "i'm", "im", "dupaco", "dupaco's", "the", "a", "an", "my", "your", "if", "please",
                "provide", "get", "tell", "give", "show", "list", "should", "will", "any", "all", "apy", "apr",
                "cd", "cds", "us", "visa", "branch", "branches", "account", "accounts", "loan", "loans", "card",
                "cards", "rate", "rates", "savings", "checking", "atm", "atms", "url", "can", "is", "was", "october", "september", "november", "december", "january", "february",
                "march", "april", "may", "june", "july", "august", "monday", "friday", "saturday", "sunday", "ok"}
# qualifiers that make a different fact: penalise a chunk carrying one the question did not ask for
QUALIFIERS = [
    (r"\bjumbo\b", 0.6), (r"\bbusiness\b", 0.75), (r"\b(ira|roth|traditional)\b", 0.6),
    (r"\brefinanc", 0.8), (r"\bintroductory|\bintro\b|balance transfer", 0.6), (r"\bspecial\b", 0.85),
    (r"\bmini-max\b", 0.6), (r"\bhealth savings|\bhsa\b", 0.7), (r"\bkids?\b|\bteen|\bminor", 0.8),
]
HEADER_SHORT = [
    (re.compile(r"Annual Percentage Yield \(APY\)", re.I), "APY"),
    (re.compile(r"Annual Percentage Rate \(As Low As\)", re.I), "APR (as low as)"),
    (re.compile(r"Annual Percentage Rate", re.I), "APR"),
]


@dataclass
class Result:
    text: str
    sources: list = field(default_factory=list)   # (url, snapshot_id, quote)
    confident: bool = True
    conflict_note: Optional[str] = None
    chunk_ids: list = field(default_factory=list)


@dataclass
class Intent:
    rate: bool
    fee: bool
    hours: bool
    count_noun: Optional[str]
    link: bool
    listing: bool
    hi: bool
    lo: bool


def intent_of(q: str) -> Intent:
    m = re.search(r"\bhow many (\w+)(?: (\w+))?", q, re.I)
    noun = None
    if m:
        noun = m.group(1).lower()
        if noun in ("dupaco", "people", "times") and m.group(2):
            noun = m.group(2).lower() if noun == "dupaco" else noun
    return Intent(
        rate=bool(RATE_INTENT_RE.search(q)) and not m,
        fee=bool(FEE_INTENT_RE.search(q)) and not m,
        hours=bool(HOURS_INTENT_RE.search(q)),
        count_noun=noun,
        link=bool(LINK_INTENT_RE.search(q)),
        listing=bool(LIST_INTENT_RE.search(q)),
        hi=bool(SUPER_HI_RE.search(q)),
        lo=bool(SUPER_LO_RE.search(q)),
    )


def _short(s: str) -> str:
    for rx, rep in HEADER_SHORT:
        s = rx.sub(rep, s)
    return s


def _entities(q: str) -> list[str]:
    """Specific names the answer must mention: capitalised words mid-sentence and 7/1-style codes."""
    out = []
    words = re.findall(r"[A-Za-z0-9$'’/&-]+", q)
    for i, w in enumerate(words):
        lw = re.sub(r"['’]s$", "", w.lower())
        if lw in GENERIC_CAPS or len(lw) < 2:
            continue
        if re.fullmatch(r"\d+/\d+", w):
            out.append(w)
        elif i > 0 and w[0].isupper():
            out.append(re.sub(r"['’]s$", "", w))
    return out


def _covers(hay: str, ent: str) -> bool:
    e = ent.lower()
    if e in hay:
        return True
    et = tokens(e, drop_stop=False)
    ht = set(tokens(hay, drop_stop=False))
    return bool(et) and all(t in ht for t in et)


def _row_has_rate(c: Chunk) -> bool:
    if c.kind == "row":
        return any(re.search(r"rate|apr|apy|yield|dividend", k, re.I) and re.search(r"\d%|\d\s?%", v)
                   for k, v in c.cells.items()) or bool(HAS_RATE_RE.search(c.text))
    return bool(HAS_RATE_RE.search(c.text))


class Answerer:
    def __init__(self, store: Store) -> None:
        self.s = store
        self.dups: dict[int, list[int]] = {}

    # ------------------------------------------------------------------ ranking
    def rank(self, question: str, si: int, it: Intent, limit: int = 80) -> list[tuple[float, int]]:
        s = self.s
        qx = expand_query(question)
        qtoks = tokens(qx)
        qset = set(qtoks)
        raw = s.bm25(qtoks, si)
        if not raw:
            return []
        ref = date.fromisoformat(s.snapshots[si][1])
        ql = question.lower()
        terms = [(int(n), u[0]) for n, u in TERM_RE.findall(ql)]
        q_money = [v for v in money_values(question) if v >= 50]
        q_year = None
        if re.search(r"\b(model|vehicle|car|auto|truck|motorcycle)\b", ql):
            ys = [int(y) for y in MODEL_YEAR_RE.findall(question)]
            q_year = ys[0] if ys else None
        ents = _entities(question)
        top = sorted(raw.items(), key=lambda kv: -kv[1])[:500]
        out = []
        shadow = s.shadowed.get(si, set())
        for cid, sc in top:
            if cid in shadow:
                continue   # a stale copy of a fact whose current value lives elsewhere
            c = s.chunks[cid]
            info = s.pages[c.url]
            f = info.authority ** 0.8 * (0.8 + 0.4 * s.recency(info, ref))
            body = f"{c.ctx} {c.text}".lower()
            head = (c.text.split(":")[0] if c.kind == "row" else c.text).lower()
            # page-title match: the page is *about* what was asked
            tt = set(tokens(info.title))
            if tt and qset:
                f *= 1 + 0.6 * len(tt & qset) / max(1, min(len(qset), len(tt)))
            for n, unit in terms:
                if re.search(rf"\b{n}[\s-]*{unit}", head if c.kind == "row" else body):
                    f *= 1.6
                elif c.kind == "row" and re.search(r"\b\d{1,3}[\s-]*(month|year)", head):
                    f *= 0.35
            if q_money and c.kind == "row":
                rng = amount_range(c.text)
                if rng:
                    f *= 1.7 if any(rng[0] <= v <= rng[1] for v in q_money) else 0.35
            if q_year is not None and c.kind == "row":
                yr = year_range(c.text)
                if yr:
                    f *= 1.7 if yr[0] <= q_year <= yr[1] else 0.35
            for pat, pen in QUALIFIERS:
                if re.search(pat, body) and not re.search(pat, ql):
                    f *= pen
            if it.rate and _row_has_rate(c):
                f *= 1.4
            if it.fee and "$" in c.text:
                f *= 1.3
            if it.hours:
                f *= 2.0 if TIME_RE.search(c.text) else 0.5
            if it.count_noun:
                f *= 2.5 if re.search(rf"\b\d[\d,]*\+? (?:\w+ ){{0,2}}{re.escape(it.count_noun[:5])}", c.text, re.I) else 0.6
            if ents and any(e.lower() in c.text.lower() for e in ents):
                f *= 1.4
            if c.text.endswith(("...", "…")):
                f *= 0.4   # teaser card of another article
            if c.kind == "row":
                f *= 1.1
            out.append((sc * f, cid))
        out.sort(key=lambda x: (-x[0], x[1]))
        # collapse the same text repeated across pages (boilerplate, shared rate tables): keep the best,
        # remember the others as corroborating sources
        first: dict[str, int] = {}
        res = []
        self.dups = {}
        for sc, cid in out:
            k = s.chunks[cid].text
            if k in first:
                self.dups.setdefault(first[k], []).append(cid)
                continue
            first[k] = cid
            if len(res) < limit:
                res.append((sc, cid))
        return res

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, si: int) -> Result:
        s = self.s
        it = intent_of(question)
        ranked = self.rank(question, si, it)
        if not ranked:
            return Result(REFUSAL_TEXT, confident=False)
        if it.link:
            return self._link_answer(question, ranked, si)
        ents = _entities(question)
        top_score = ranked[0][0]
        best = None
        for sc, cid in ranked[:50]:
            if sc < 0.2 * top_score:
                break
            c = s.chunks[cid]
            info = s.pages[c.url]
            hay = f"{c.ctx} {c.text}".lower() + " " + info.title.lower()
            if ents and not all(_covers(hay, e) for e in ents):
                continue
            if it.rate and not (_row_has_rate(c) and info.kind in ("rate_sheet", "product", "landing", "home")):
                continue
            if it.fee and not it.rate and "$" not in c.text:
                continue
            if it.hours and not TIME_RE.search(c.text):
                continue
            best = (sc, cid)
            break
        if best is None or best[0] < 3.0:
            return Result(REFUSAL_TEXT, confident=False)
        sc, cid = best
        text, used = self._compose(question, it, cid, sc, ranked, si)
        return Result(text, sources=self._sources(used, ranked, si), confident=True, chunk_ids=used)

    def _compose(self, question: str, it: Intent, cid: int, sc: float, ranked, si: int):
        s = self.s
        c = s.chunks[cid]
        if c.kind == "row" and c.table:
            scores = {k: v for v, k in ranked}
            rows = s.rows_of(c.table, si)
            if it.hi or it.lo:
                def last_pct(r):
                    vals = re.findall(r"(\d+(?:\.\d+)?)\s?%", s.chunks[r].text)
                    return float(vals[-1]) if vals else None
                cand = [r for r in rows if last_pct(r) is not None]
                if cand:
                    pick = (max if it.hi else min)(cand, key=lambda r: (last_pct(r), -r))
                    return self._render_rows([pick]), [pick]
            keep = [r for r in rows if scores.get(r, 0) >= 0.8 * sc]
            if cid not in keep:
                keep.insert(0, cid)
            keep = sorted(keep, key=lambda r: rows.index(r) if r in rows else 0)[:6]
            parts, used = [self._render_rows(keep)], list(keep)
            if it.listing:
                # a question about a family of products: one block per distinct rate table that ranks well
                seen_tables = {c.table}
                for osc, ocid in ranked[:40]:
                    oc = s.chunks[ocid]
                    if len(seen_tables) >= 4 or osc < 0.35 * sc:
                        break
                    if oc.kind != "row" or oc.table in seen_tables or s.pages[oc.url].kind not in ("rate_sheet", "product"):
                        continue
                    if not _row_has_rate(oc):
                        continue
                    seen_tables.add(oc.table)
                    orows = [r for r in s.rows_of(oc.table, si) if scores.get(r, 0) >= 0.6 * osc] or [ocid]
                    parts.append(self._render_rows(orows[:6]))
                    used.extend(orows[:6])
            return " ".join(parts), used
        if c.kind == "group" and it.hours:
            # several schedules on file (branches differ): give the two most relevant distinct ones
            groups = [cid]
            for osc, ocid in ranked[:20]:
                oc = s.chunks[ocid]
                if ocid != cid and oc.kind == "group" and TIME_RE.search(oc.text) and osc >= 0.5 * sc \
                        and oc.text.split(":")[0] == c.text.split(":")[0]:
                    groups.append(ocid)
                    break
            return " Schedules vary by branch. ".join(s.chunks[g].text for g in groups) if len(groups) > 1 \
                else c.text, groups
        txt = c.text
        if len(txt) > 360:
            txt = self._best_sentences(question, txt)
        return txt, [cid]

    def _render_rows(self, rids: list[int]) -> str:
        s = self.s
        first = s.chunks[rids[0]]
        cap = first.ctx.split(" > ")[-1] if first.ctx else ""
        parts = [_short(s.chunks[r].text) for r in rids]
        lead = f"{cap}: " if cap and cap.lower() not in parts[0].lower() else ""
        return lead + "; ".join(parts) + "."

    def _best_sentences(self, question: str, txt: str, k: int = 2) -> str:
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", txt)
        q = set(tokens(expand_query(question)))
        scored = [(len(q & set(tokens(x))), -i, x) for i, x in enumerate(sents)]
        top = sorted(scored, reverse=True)[:k]
        keep = sorted(top, key=lambda t: -t[1])
        return " ".join(x for _, _, x in keep)[:600]

    def _sources(self, used: list[int], ranked, si: int) -> list:
        s = self.s
        sid = s.snapshots[si][0]
        out, seen = [], set()

        def add(cid):
            c = s.chunks[cid]
            if c.url not in seen and len(out) < 6:
                seen.add(c.url)
                out.append((c.url, sid, c.text[:200]))

        for cid in used:
            add(cid)
        # pages carrying the very same fact (shared rate tables, boilerplate) corroborate it
        for cid in used:
            for d in self.dups.get(cid, []):
                if s.pages[s.chunks[d].url].kind in ("rate_sheet", "product", "landing", "home", "document"):
                    add(d)
        for cid in used:
            for d in self.dups.get(cid, []):
                add(d)
        return out

    def _link_answer(self, question: str, ranked, si: int) -> Result:
        """'Which link/URL…' -> the product page that best matches, scored at page level."""
        s = self.s
        page_score: dict[str, float] = {}
        for sc, cid in ranked[:60]:
            c = s.chunks[cid]
            info = s.pages[c.url]
            if info.kind not in ("product", "landing", "home"):
                continue
            page_score[c.url] = page_score.get(c.url, 0.0) + sc
        if not page_score:
            return Result(REFUSAL_TEXT, confident=False)
        url = max(page_score, key=lambda u: (page_score[u], u))
        clean = url.split("?")[0]
        return Result(f"You can find it here: {clean}", sources=[(clean, s.snapshots[si][0], None)])
