"""Question -> one grounded answer. Rerank BM25 hits by authority, recency and qualifiers;
refuse when the site does not say; compose a short answer with one value per fact."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .chunker import Chunk
from .store import Store, row_key, row_values
from .text import (amount_range, expand_query, money_values, numwords_to_digits, stem, term_patterns, tokens,
                   year_range)

REFUSAL_TEXT = "I don't know: that isn't listed on the website. Please contact us and a team member can help."

RATE_INTENT_RE = re.compile(r"\b(rates?|aprs?|apys?|dividends?|yield|interest)\b", re.I)
FEE_INTENT_RE = re.compile(r"\b(fees?|cost|costs|charge|price|how much)\b", re.I)
HOURS_INTENT_RE = re.compile(r"\bhours\b|\bwhat time\b|\bopen (on|until)\b", re.I)
TIME_RE = re.compile(r"\b\d{1,2}(:\d\d)?\s?(a\.?m\.?|p\.?m\.?)|\bnoon\b", re.I)


def _has_hours(c) -> bool:
    """A passage that states hours: a time, or "Closed" in a row of an hours table."""
    return bool(TIME_RE.search(c.text)) or (c.hours_table and "closed" in c.text.lower())
LIST_INTENT_RE = re.compile(r"\b(what|which) (\w+ ){0,3}(do|does) (you|dupaco) (offer|have)\b|\blist\b|\ball (of )?(the|your)\b|"
                            r"\b(rates|aprs|apys) (for|on) (the|your)\b|\bwhat are (the|your|their) (\w+ ){0,3}(rates|aprs|apys)\b|"
                            r"\b(\w+ )?(rates|aprs|apys)\b.*\b(right now|today)\b|\bprovide (\w+ ){0,3}rates\b", re.I)
HAS_RATE_RE = re.compile(r"\d+(?:\.\d+)?\s?%\s*(?:\(?apr\)?|\(?apy\)?|annual percentage|fixed rate|variable rate|dividend)|"
                         r"(?:rates?|apr|apy|yield)\s*(?:of|is|at|:|=|as low as|from)?\s*\d+(?:\.\d+)?\s?%", re.I)
MIN_COVERAGE = 0.25
CTA_RE = re.compile(r"^(for more information|to learn more|learn more|click|visit|download|get started|apply|see details|"
                    r"open my|call to|keep reading|read more)\b", re.I)
SUBJECT_RE = re.compile(r"\b([a-z][a-z$/-]+)\s+(loans?|mortgages?|accounts?|cards?|certificates?|cds?|lines? of credit|savings|checking|ira)\b", re.I)
SUBJECT_SKIP = {"a", "an", "the", "my", "your", "our", "this", "that", "for", "on", "of", "any", "new", "used", "fixed",
                "special", "month", "year", "rate", "current", "standard", "regular", "dupaco", "dupaco's", "per", "what",
                "which", "is", "are", "do", "does", "share", "credit", "debit", "loan", "money", "term", "jumbo"}
SUBJECT_SYN = {"car": "auto", "vehicle": "auto", "truck": "auto", "house": "home", "boat": "boat", "rv": "rv",
               "college": "student", "school": "student", "mortgage": "home"}
ADDRESS_RE = re.compile(r"\baddress\b|\bwhere is\b|\blocated\b", re.I)
DESCRIPTIVE_RE = re.compile(r"^(what is|what's|what are|tell me about|describe|can i|do you|does dupaco|is there|how does)\b", re.I)
NUMBER_Q_RE = re.compile(r"\b(routing|phone|telephone|fax|account|aba|nmls)\s*(number|no\.?|#)|\brouting\b|"
                         r"\bnumber\b.{0,30}\bcall\b|\bcall\b.{0,30}\bnumber\b|\bwho do i call\b", re.I)
DATE_PHRASE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2},?\s+)?(19|20)\d\d\b|"
                            r"\b(19|20)\d\d-\d\d-\d\d\b|\b(in|of|during|since)\s+(19|20)\d\d\b", re.I)
# free-text award facts: the organisation that recognised the institution (the newest dated statement wins)
ORG_RE = re.compile(r"\b(Forbes|Newsweek|BauerFinancial|Bauer|S&P Global|Des Moines Register|Credit Union Journal|"
                    r"J\.D\. Power|Money Magazine)\b")
AWARD_RE = re.compile(r"(?i)\b(recogni(?:[sz]|tion)\w*|awards?|awarded|rank(?:ed|ing|s)?|named|honou?r\w*|best|top)\b")
ORDINAL_TIME_RE = re.compile(r"(?i)\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))"
                             r" (?:consecutive )?(?:time|year)\b")
# a sentence that refers back to the entity of the sentence before it ("This initiative provides up to $15,000")
ANAPHOR_RE = re.compile(r"^(?:This|These|The)\s+(?:new\s+)?(?:initiative|program|programme|offer|grant|loan|account|"
                        r"card|benefit|service|fund|product|partnership|award|recognition)s?\b")
# a yes/no check of a stated value ("Does any club account earn 1.25% APY?", "Is there a certificate at 5%?")
VERIFY_RE = re.compile(r"(?i)^\s*(?:do|does|is|are) (?:any|there|dupaco|you)\b.*\b(?:pay|earn|offer|yield|get|at)\b[^?]*"
                       r"\d(?:\.\d+)?\s?%")
COMPARE_RE = re.compile(r"(?i)\b(?:more|less|higher|lower|greater|fewer) than\b|\bat (?:least|most)\b|\b(?:above|below|over|"
                        r"under|up to|exceed\w*|or (?:more|less|higher|lower))\b")


def _verify(q: str) -> bool:
    """A yes/no check of one stated value (short questions only: the pattern backtracks on long input)."""
    return len(q) <= 300 and bool(VERIFY_RE.search(q))


HISTORY_RE = re.compile(r"\b(expired|ended|past|previous|former|used to|old)\b", re.I)
PROCEDURE_RE = re.compile(r"\bhow (do|can|would) i\b|\bwhere (do|can) i\b|\bonline\b", re.I)
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
    org: Optional[str] = None      # a recognition question names the organisation that gave it


_NOT_ORG = frozenset("I Dupaco Credit Union Community What Which Who When How Has Have Is Are Does Did Do Can The A An "
                    "Visa Mastercard".split())


def _award_org(q: str) -> Optional[str]:
    """The organisation a recognition question names: a known ranking body, else a capitalised name that is not
    the institution itself or a question word ("Which honors has Kiplinger given Dupaco?" -> "Kiplinger")."""
    if not AWARD_RE.search(q):
        return None
    m = ORG_RE.search(q)
    if m:
        return m.group(1)
    for name in re.findall(r"\b([A-Z][\w&.'-]+(?:\s+[A-Z][\w&.'-]+)*)", q):
        words = [w for w in name.split() if w not in _NOT_ORG]
        if words:
            return " ".join(words)
    return None


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
        org=_award_org(q),
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


HEAD_NOUNS = [
    (re.compile(r"\bmortgages?\b|\bhome loans?\b", re.I), re.compile(r"mortgage|home loan|conventional|purchase|refinanc", re.I)),
    (re.compile(r"\bcertificates?\b|\bcds?\b", re.I), re.compile(r"certificate|\bcd\b|month", re.I)),
    (re.compile(r"\bcredit cards?\b|\bvisa\b", re.I), re.compile(r"visa|card", re.I)),
]


def _heads_covered(q: str, hay: str) -> bool:
    return all(body.search(hay) for ask, body in HEAD_NOUNS if ask.search(q))


def _model_year(question: str) -> Optional[int]:
    """The vehicle model year a question names (not a date like 'August 2026')."""
    if not re.search(r"\b(model|vehicle|car|auto|truck|motorcycle)\b", question, re.I):
        return None
    ys = [int(y) for y in MODEL_YEAR_RE.findall(_strip_dates(question))]
    return ys[0] if ys else None


def _strip_dates(q: str) -> str:
    return DATE_PHRASE_RE.sub(" ", q)


def _subjects(q: str) -> list[str]:
    """The product a rate/fee question is about ('construction' in 'construction loan')."""
    out = []
    for m in SUBJECT_RE.finditer(q):
        w = m.group(1).lower()
        if w in SUBJECT_SKIP or re.search(r"\d", w):
            continue
        out.append(SUBJECT_SYN.get(w, w))
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
        qx = expand_query(_strip_dates(question))
        if _verify(question):
            # "Does any club account earn 1.25% APY?": the value is what to check, not what to look up
            qx = re.sub(r"\d+(?:\.\d+)?\s?%", " ", qx)
        qtoks = tokens(qx)
        qset = set(qtoks)
        raw = s.bm25(qtoks, si)
        if not raw:
            return []
        ref = date.fromisoformat(s.view_date(si))
        ql = question.lower()
        terms = [(int(n), u[0]) for n, u in TERM_RE.findall(ql)]
        q_money = [v for v in money_values(question) if v >= 50]
        q_year = None
        q_year = _model_year(question)
        ents = _entities(question)
        address_q = bool(ADDRESS_RE.search(question))
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
            # a row's qualifiers are all its non-value cells (term, tier, model year), not just the first one
            head = (" ".join(v for v in c.cells.values() if "%" not in v) if c.kind == "row" else c.text).lower()
            # page-title / heading match: the page or section is *about* what was asked
            tt = set(tokens(info.title))
            if tt and qset:
                f *= 1 + 0.6 * len(tt & qset) / max(1, min(len(qset), len(tt)))
            ct = set(tokens(c.ctx))
            if ct and qset:
                f *= 1 + 0.4 * len(ct & qset) / max(1, len(qset))
            if c.kind in ("para", "sent"):
                nw = len(c.text.split())
                f *= 0.5 + 0.5 * min(1.0, nw / 10)
                if CTA_RE.match(c.text):
                    f *= 0.6
            if ents and any(e.lower() in info.title.lower() for e in ents):
                f *= 1.3
            if address_q and re.search(r"\b(location|branch)", c.ctx, re.I):
                f *= 1.5
            for n, unit in terms:
                if any(re.search(p, head if c.kind == "row" else body) for p in term_patterns(n, unit)):
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
                f *= 2.0 if _has_hours(c) else 0.5
            if it.count_noun:
                f *= 2.5 if re.search(rf"\b\d[\d,]*\+? (?:\w+ ){{0,2}}{re.escape(it.count_noun[:5])}", c.text, re.I) else 0.6
            if ents and any(e.lower() in c.text.lower() for e in ents):
                f *= 1.4
            if NUMBER_Q_RE.search(question):
                f *= 2.0 if re.search(r"\d{7,}|\d{3}-\d{3}-\d{4}", c.text) else 0.7
            if c.text.endswith(("...", "…")):
                f *= 0.4   # teaser card of another article
            if c.kind == "row":
                f *= 1.1
            if not s.present(c.url, si):
                f *= 0.6    # page missing from this crawl: carried forward from an older one
            if s.expired(cid, si):
                f *= 0.15   # the offer says it has ended
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
        question = numwords_to_digits(question)
        it = intent_of(question)
        ranked = self.rank(question, si, it)
        if not ranked:
            return Result(REFUSAL_TEXT, confident=False)
        if it.link:
            return self._link_answer(question, ranked, si)
        ents = _entities(question)
        subj = _subjects(question)
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
            if (it.rate or it.fee) and subj and not all(_covers(f"{c.ctx} {c.text}".lower(), x) for x in subj):
                continue
            if it.rate and not self._tier_fits(question, c):
                continue
            if (it.rate or it.fee) and not _heads_covered(question, hay):
                continue
            if it.fee and not it.rate and "$" not in c.text:
                continue
            if it.hours and not _has_hours(c):
                continue
            if s.expired(cid, si) and not HISTORY_RE.search(question):
                continue   # an ended offer never answers a question about today
            best = (sc, cid)
            break
        if best is None or self.coverage(question, best[1]) < MIN_COVERAGE:
            return Result(REFUSAL_TEXT, confident=False)
        if it.count_noun:
            best = self._latest_statement(best, ranked, ents, it.count_noun)
        elif it.org:
            best = self._latest_award(best, ranked, it.org)
        sc, cid = best
        text, used = self._compose(question, it, cid, sc, ranked, si)
        text = self._titled(text, cid)
        lead = self._verify_lead(question, cid, si)
        if lead:
            text = f"{lead} {text}"
        if PROCEDURE_RE.search(question):
            link = self._action_link(question, cid, si)
            if link and link not in text:
                text = f"{text} You can do it online at {link}"
        return Result(text, sources=self._sources(used, ranked, si), confident=True, chunk_ids=used,
                      conflict_note=self._conflict_note(used, si))

    def _conflict_note(self, used: list[int], si: int) -> Optional[str]:
        """Why this value and not the others the site shows (kept out of `text` on purpose)."""
        s = self.s
        groups = s.fact_groups.get(si, {})
        shadow = s.shadowed.get(si, set())
        notes = []
        for cid in used:
            c = s.chunks[cid]
            k = row_key(c)
            if not k or k not in groups:
                continue
            losers = [x for x in groups[k] if x in shadow]
            if not losers:
                continue
            label = c.text.split(':')[0]
            if any(n.startswith(label + ":") for n in notes):
                continue
            reasons = "; ".join(dict.fromkeys(s.why_wins(cid, x, si) for x in losers[:3]))
            notes.append(f"{label}: other pages show a different value; used "
                         f"{c.url.replace('https://www.dupaco.com', '') or '/'} because of {reasons}.")
            if len(notes) >= 2:
                break
        return " ".join(notes) or None

    def _latest_statement(self, best, ranked, ents, noun: str):
        """Counts ("how many branches") change over time: the most recently published statement wins."""
        s = self.s
        rx = re.compile(rf"\b\d[\d,]*\+?\s(?:[\w-]+\s){{0,2}}{re.escape(stem(noun)[:5])}", re.I)
        pool = []
        for sc, cid in ranked[:40]:
            c = s.chunks[cid]
            if sc < 0.4 * best[0] or not rx.search(c.text):
                continue
            hay = f"{c.ctx} {c.text}".lower()
            if ents and not all(_covers(hay, e) for e in ents):
                continue
            info = s.pages[c.url]
            live = info.kind in ("product", "home", "rate_sheet", "landing")
            pool.append((info.pub_date or (date.max if live else date.min), sc, cid))
        dated = [p for p in pool if p[0] not in (date.max, date.min)]
        pool = dated or pool   # a dated statement beats undated copy on a live page (old bios, stale blurbs)
        if not pool:
            return best
        _, sc, cid = max(pool, key=lambda t: (t[0], t[1]))
        return (sc, cid)

    def _latest_award(self, best, ranked, org: str):
        """Recognitions repeat yearly ("the fourth time", "the fifth time"): the most recently published
        statement naming the organisation and an award word wins, as for counts."""
        s = self.s
        pool = []
        for sc, cid in ranked[:80]:
            c = s.chunks[cid]
            if c.kind not in ("para", "sent") or org not in c.text or not AWARD_RE.search(c.text):
                continue
            info = s.pages[c.url]
            if not info.pub_date:
                continue
            # a statement about the recognition (its own release, or "the Nth time") outranks a passing mention
            # in boilerplate ("It is a Newsweek best regional credit union ...")
            ordinal = bool(ORDINAL_TIME_RE.search(c.text))
            pool.append((org in info.title or ordinal, info.pub_date, ordinal, sc, cid))
        if not pool:
            return best
        *_, sc, cid = max(pool, key=lambda t: t[:4])
        return (sc, cid)

    def _verify_lead(self, question: str, cid: int, si: int) -> Optional[str]:
        """"Yes" / "No" for a question that checks one stated value against a product's table ("Do any of the
        club savings tiers earn 1.25% APY?"). Only the column asked is compared, over every tier of the table, and a
        value inside a stated range counts. Comparisons ("at least", "more than") get no lead."""
        s = self.s
        c = s.chunks[cid]
        vm = re.search(r"(\d+(?:\.\d+)?)\s?%\s*(APY|APR|dividend|rate)?", question, re.I)
        if not (_verify(question) and vm) or COMPARE_RE.search(question) or c.kind != "row" or not c.table:
            return None
        v = float(vm.group(1))
        unit = (vm.group(2) or "").lower()
        col = "apy" if unit == "apy" else "apr" if unit == "apr" else "div" if unit == "dividend" else None
        vals = []
        for r in s.rows_of(c.table, si):
            for k, nums in row_values(s.chunks[r]):
                if col is None or k.split("-")[0] == col:
                    vals.append(nums)
        if not vals:
            return None
        hit = any(v in nums or (len(nums) == 2 and nums[0] <= v <= nums[1]) for nums in vals)
        return f"Yes: {v:g}% is listed." if hit else f"No: none of these tiers pays {v:g}%."

    def _continuation(self, cid: int, si: int) -> Optional[str]:
        """The sentence right after chunk `cid` in its paragraph (as shown in view `si`) when it refers back to it
        (entity resolution in free text: "This grant ..." is about the program the sentence before named)."""
        s = self.s
        c = s.chunks[cid]
        tail = c.text.strip()[-80:]
        bit = 1 << si
        for pid in s.url_chunks.get(c.url, []):
            p = s.chunks[pid]
            if p.kind != "para" or pid == cid or tail not in p.text or not s.chunk_view[pid] & bit:
                continue
            after = p.text[p.text.index(tail) + len(tail):].strip()
            nxt = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", after, maxsplit=1)[0] if after else ""
            if nxt and ANAPHOR_RE.match(nxt) and len(nxt) <= 360:
                return nxt
        return None

    def coverage(self, question: str, cid: int) -> float:
        """Share of the question's information (idf mass) that the passage and its context mention."""
        s = self.s
        qt = set(tokens(_strip_dates(question)))
        if not qt:
            return 0.0
        have = s.chunk_tf[cid]
        tot = sum(s.idf(t) for t in qt)
        return sum(s.idf(t) for t in qt if t in have) / tot if tot else 0.0

    def _tier_fits(self, question: str, c: Chunk) -> bool:
        """A rate answer must match the term / amount the member named; otherwise the site doesn't say."""
        ql = _strip_dates(question).lower()
        body = f"{c.url} {c.ctx} {c.text}".lower()   # the URL carries scope (/business-rates)
        for pat, _ in QUALIFIERS:
            if re.search(pat, ql) and not re.search(pat, body):
                return False
        for n, unit in TERM_RE.findall(ql):
            if not any(re.search(p, body) for p in term_patterns(int(n), unit[0])):
                return False
        yq = _model_year(question)
        if yq is not None and c.kind == "row":
            yr = year_range(c.text)
            if yr and not yr[0] <= yq <= yr[1]:
                return False
        amounts = [v for v in money_values(question) if v >= 50]
        if amounts and c.kind == "row":
            rng = amount_range(c.text)
            if rng and not any(rng[0] <= v <= rng[1] for v in amounts):
                return False
        return True

    def _titled(self, text: str, cid: int) -> str:
        """Name the product the answer is about when the snippet itself doesn't (no numbers added)."""
        c = self.s.chunks[cid]
        info = self.s.pages[c.url]
        if c.kind in ("row",) or info.kind not in ("product", "landing"):
            return text
        title = re.split(r"\s[|–-]\s", info.title)[0].strip()
        if not title or re.search(r"\d", title) or len(title) > 60:
            return text
        if set(tokens(title)) <= set(tokens(text)):
            return text
        return f"{title}: {text}"

    def _action_link(self, question: str, cid: int, si: int) -> Optional[str]:
        """'How do I join online?' -> the call-to-action link on the answering page that fits the question."""
        s = self.s
        url = s.chunks[cid].url
        q = set(tokens(expand_query(question)))
        best, best_sc = None, 0.0
        for ocid in s.page_chunks(url, si):
            oc = s.chunks[ocid]
            for link in oc.links:
                if not link.startswith("http") or "dupaco.com" not in link or link.rstrip("/") == url.rstrip("/"):
                    continue
                if "#" in link or "privacy" in link:
                    continue
                host_sub = not link.split("/")[2].startswith("www.")
                sc = len(q & set(tokens(oc.text + " " + link.replace("/", " ").replace(".", " ")))) + (1.5 if host_sub else 0)
                if sc > best_sc:
                    best, best_sc = link, sc
        return best if best_sc >= 2 else None

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
            label = c.text.split(":")[0]
            seen_sched = {re.sub(r"\W", "", c.text.lower())}
            for osc, ocid in ranked[:30]:
                oc = s.chunks[ocid]
                key = re.sub(r"\W", "", oc.text.lower())
                if ocid != cid and oc.kind == "group" and TIME_RE.search(oc.text) and osc >= 0.4 * sc \
                        and oc.text.split(":")[0] == label and key not in seen_sched:
                    groups.append(ocid)
                    seen_sched.add(key)
                if len(groups) >= 3:
                    break
            return " Schedules vary by branch. ".join(s.chunks[g].text for g in groups) if len(groups) > 1 \
                else c.text, groups
        txt = c.text
        if it.fee and len(re.findall(r"\$\s?\d", txt)) >= 3:
            txt = self._best_clause(question, txt)
        elif len(txt) > 360:
            txt = self._best_sentences(question, txt)
        used = [cid]
        if it.org and len(txt) > 200:
            # a recognition: the sentence naming the organisation and how many times it has recognised us
            sents = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", c.text)
            hits = [x for x in sents if it.org in x or ORDINAL_TIME_RE.search(x)]
            if hits:
                txt = " ".join(hits[:2])[:600]
        if DESCRIPTIVE_RE.search(question.strip()) and not (it.rate or it.fee or it.count_noun or it.hours):
            # a descriptive question: add the next-best passage from the same page
            for osc, ocid in ranked[:15]:
                oc = s.chunks[ocid]
                if ocid == cid or oc.url != c.url or osc < 0.75 * sc or oc.kind not in ("para", "sent"):
                    continue
                if oc.text in txt or txt in oc.text or CTA_RE.match(oc.text):
                    continue
                extra = oc.text if len(oc.text) <= 360 else self._best_sentences(question, oc.text)
                txt = f"{txt} {extra}"
                used.append(ocid)
                break
            # each quoted passage brings the sentence that refers back to it, so the facts stay with their entity
            for u in list(used):
                nxt = self._continuation(u, si)
                if nxt and nxt not in txt:
                    txt = f"{txt} {nxt}"
        return txt, used

    def _render_rows(self, rids: list[int]) -> str:
        s = self.s
        first = s.chunks[rids[0]]
        cap = first.ctx.split(" > ")[-1] if first.ctx else ""
        parts = [_short(s.chunks[r].text) for r in rids]
        lead = f"{cap}: " if cap and not re.search(r"\d", cap) and cap.lower() not in parts[0].lower() else ""
        body = "; ".join(parts)
        return lead + body + ("" if body.endswith(".") else ".")

    def _best_clause(self, question: str, txt: str) -> str:
        """A flattened fee list ('Official check $5.00 each Domestic wire $25.00 each ...'): the one item asked."""
        q = set(tokens(expand_query(question)))
        best, best_sc = txt[:300], 0
        for m in re.finditer(r"([^$]{3,90}?)\s*(\$\s?\d[\d,]*(?:\.\d+)?\*?(?:\s*(?:each|per \w+))?)", txt):
            label = re.split(r"(?<=each)|(?<=month\))|(?<=inquiry)", m.group(1))[-1].strip(" .;,")
            sc = sum(self.s.idf(t) for t in q & set(tokens(label)))
            if sc > best_sc:
                best, best_sc = f"{label}: {m.group(2).strip()}", sc
        return best

    def _best_sentences(self, question: str, txt: str, k: int = 2) -> str:
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", txt)
        q = set(tokens(expand_query(question)))
        scored = [(len(q & set(tokens(x))), -i, x) for i, x in enumerate(sents)]
        top = sorted(scored, reverse=True)[:k]
        keep = sorted(top, key=lambda t: -t[1])
        return " ".join(x for _, _, x in keep)[:600]

    def _sources(self, used: list[int], ranked, si: int) -> list:
        s = self.s
        out, seen = [], set()

        def add(cid):
            c = s.chunks[cid]
            if c.url not in seen and len(out) < 6:
                seen.add(c.url)
                out.append((c.url, s.view_sid(si, cid), c.text[:200]))

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
        keyvals = set(re.findall(r"\d+(?:\.\d+)?%|\b\d{6,}\b", " ".join(s.chunks[u].text for u in used)))
        if keyvals:
            for sc, cid in ranked[:40]:
                c = s.chunks[cid]
                if s.pages[c.url].kind in ("rate_sheet", "product", "landing", "home") and \
                        keyvals & set(re.findall(r"\d+(?:\.\d+)?%|\b\d{6,}\b", c.text)):
                    add(cid)
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
        return Result(f"You can find it here: {clean}", sources=[(clean, s.view_sid(si), None)])
