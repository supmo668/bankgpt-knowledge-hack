"""Free-text facts (v2, see NOTES.md "Free-text facts"): rate / APY / APR / dividend statements in paragraphs,
resolved to the same fact key the tables use and checked against the table value in the same view.

A *claim* is one value a sentence states ("earn 3.66% APY", "If you borrow $30,000 at a 5.39% APR with a
60-month term"), with what qualifies it: the column (APY, APR, dividend rate, rate), intro or standard, the term
in months, a balance or loan amount, and a stated "as of" date. Entity resolution maps the claim to table fact
keys in four steps:

  1. column: the key's winning row has a value in the claim's column;
  2. intro: an intro claim only matches intro keys/columns when the product has any, a standard claim never does;
  3. term and amount: a stated term must be the key's term; a stated amount must fall inside the key's tier;
  4. product: the most product words shared by the key and the claim's sentence, heading, page title and URL,
     at least one of them a distinctive word (not "loan", "credit", "savings" ...).

Ties are kept: a claim that names no tier resolves to all tiers of its product, and it agrees with the table
when any of them carries its value. Statements of bounds (floor, cap, "never less than", prime-plus margins),
hypothetical figures and other institutions' numbers are not claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .chunker import SENT_SPLIT_RE
from .store import Store, row_values
from .text import amount_range, money_values

CLAIM_PAGE_KINDS = ("rate_sheet", "product", "landing", "home", "newsroom", "document")
_P = r"(\d{1,2}(?:\.\d{1,3})?)\s?%"
# "15.50%–21.00% APR", "3.66% APY", "4.00% dividend rate", "5.39% annual percentage rate (APR)", "2.90% intro APR"
VALUE_RE = re.compile(rf"{_P}(?:\s?(?:–|-|—|to)\s?{_P})?\s*(?:\*|\d(?=\s))?\s*\(?\s*"
                      r"(intro(?:ductory)?\s+)?(APY|APR|annual percentage (?:yield|rate)|dividend rate)\b", re.I)
# "Variable Annual Percentage Rate (APR) of 20.5%", "APR of 15.50%"
VALUE_OF_RE = re.compile(rf"\b(APY|APR|annual percentage (?:yield|rate)(?: \((?:APY|APR)\))?)(?:\s+of|:)\s+{_P}"
                         rf"(?:\s?(?:–|-|—|to)\s?{_P})?", re.I)
# "12.50% APR to 19.50% APR" -> "12.50% to 19.50% APR"
UNIT_RANGE_RE = re.compile(r"(?i)(\d\s?%)\s*(APR|APY)\s+to\s+(\d{1,2}(?:\.\d{1,3})?\s?%)\s*\2")
INTRO_BEFORE_RE = re.compile(r"(?i)\bintro(?:ductory)?\b[^.%]{0,30}$")
INTRO_AFTER_RE = re.compile(r"(?i)^[^.%]{0,45}\b(?:intro(?:ductory)?|balance transfer)\b")
# a dollar amount that qualifies the value: "borrow $30,000", "at least $25,000", "$25,000.00-99,999.99 at 4.00%"
AMOUNT_RE = re.compile(r"(?i)(?:borrow(?:ed)?|at least|balances? (?:of|over)|deposit of|with)\s+\$\s?([\d,]+(?:\.\d+)?)(?![\d,]*\s?(?:cash|bonus|back))"
                       r"|\$\s?([\d,]+(?:\.\d+)?)\s?[-–]\s?\$?\s?([\d,]+(?:\.\d+)?)\s+at\b[^$;]{0,40}$")
TERM_RE = re.compile(r"(?i)\b(\d{1,3})[- ]?(?:months?|mo)\b")
KEY_TERM_RE = re.compile(r"\b(\d{1,3})[- ]?(?:months?|mo)\b")
NOT_A_CLAIM_RE = re.compile(r"(?i)\b(floor|never (?:be )?(?:less|exceed|more)|prime rate|margin|below the (?:current )?market|"
                            r"cap(?:ped)?\b|maximum|minimum rate|as little as|for instance|imagine|hypothetical|average|"
                            r"national|nationwide|industry|(?:does not|doesn't|do not) qualify)\b")
STOP = frozenset("the a an of and or for to in on with at your our you month months mo year years term "
                 "rate rates apy apr dividend fixed conventional less more than up new get earn open from is are "
                 "this that it its as low be will just only all any dupaco www com https http lp tier model".split())
GENERIC = frozenset("loan loans credit savings account accounts card cards rate line money market home checking "
                    "certificate certificates special share deposit annual percentage yield standard".split())
WORD_RE = re.compile(r"[a-z][a-z0-9&'$-]*")
_INTRO_KEY_RE = re.compile(r"(?i)intro|promo|balance transfer")


def _col(unit: str) -> str:
    u = unit.lower()
    if "apy" in u or "yield" in u:
        return "apy"
    if "dividend" in u:
        return "div"
    return "apr"


@dataclass(frozen=True)
class Claim:
    cid: int                    # paragraph chunk the sentence is in
    sentence: str
    quote: str                  # the matched value phrase
    col: str                    # apy | apr | div
    value: tuple                # (n,) or (lo, hi)
    intro: bool
    terms: tuple                # months stated in the sentence
    amount: Optional[float]     # the dollar amount nearest before the value (balance / loan tier)
    effective: Optional[str]    # "As of 8/24/26" stated in the sentence (M/D/YY), else None


def _nearest_amount(sentence: str, pos: int) -> Optional[float]:
    """The dollar amount that qualifies the value at `pos`: a tier right before it ("$25,000.00-99,999.99 at
    4.00%"), else the last "borrow $X" / "at least $X" in its clause. A cash bonus is not a balance."""
    head = sentence[:pos]
    m = AMOUNT_RE.search(head[-100:])
    if m and m.group(2):
        lo, hi = float(m.group(2).replace(",", "")), float(m.group(3).replace(",", ""))
        return (lo + hi) / 2
    clause = re.split(r"[;]|\. ", head)[-1]
    found = [mm for mm in AMOUNT_RE.finditer(clause) if mm.group(1)]
    return float(found[-1].group(1).replace(",", "")) if found else None


def extract_claims(cid: int, text: str) -> list[Claim]:
    out = []
    eff = re.search(r"(?i)\bas of,?\s+(\d{1,2}/\d{1,2}/\d{2,4})", text)
    for sent in SENT_SPLIT_RE.split(UNIT_RANGE_RE.sub(r"\1 to \3 \2", text)):
        if NOT_A_CLAIM_RE.search(sent) or len(sent) > 600:
            continue
        terms = tuple(int(t) for t in TERM_RE.findall(sent))
        hits = []
        for m in VALUE_RE.finditer(sent):
            a, b = float(m.group(1)), (float(m.group(2)) if m.group(2) else None)
            intro = bool(m.group(3)) or bool(INTRO_BEFORE_RE.search(sent[:m.start()])) \
                or bool(INTRO_AFTER_RE.search(sent[m.end():]))
            hits.append((m.start(), m.group(0), _col(m.group(4)), (a, b) if b is not None else (a,), intro))
        for m in VALUE_OF_RE.finditer(sent):
            intro = bool(INTRO_BEFORE_RE.search(sent[:m.start()])) or bool(INTRO_AFTER_RE.search(sent[m.end():]))
            val = (float(m.group(2)), float(m.group(3))) if m.group(3) else (float(m.group(2)),)
            hits.append((m.start(), m.group(0), _col(m.group(1)), val, intro))
        for pos, quote, col, value, intro in hits:
            if any(v > 40 for v in value):
                continue
            out.append(Claim(cid, sent.strip(), quote.strip(), col, value, intro, terms,
                             _nearest_amount(sent, pos), eff.group(1) if eff else None))
    return out


def _words(s: str) -> set:
    return set(WORD_RE.findall(s.lower())) - STOP


def _key_product(key: str) -> str:
    """The product part of a fact key: drop scope, tiers, terms and model-year aliases."""
    parts = key.split(" | ")[1:]
    keep = [p for p in parts if not re.search(r"\d", p) or re.search(r"[a-z]{4,}", re.sub(r"\d+[- ]?(months?|mo)\b", "", p))]
    return " ".join(re.sub(r"\$[\d.,+-]+|\d+[- ]?(?:months?|mo)\b|model-year tier \d+|\([^)]*\)", " ", p) for p in keep)


def _tier(key: str) -> Optional[tuple]:
    m = re.search(r"\$[\d.]+(?:-\$?[\d.]+|\+)", key)
    if not m:
        return None
    t = m.group(0)
    if t.endswith("+"):
        return float(t[1:-1]), float("inf")
    lo, hi = t[1:].split("-")
    return float(lo), float(hi.lstrip("$"))


class TextFacts:
    """Claims per paragraph chunk (cached) and their resolution against one view's table facts."""

    def __init__(self, store: Store) -> None:
        self.s = store
        self._claims: dict[int, list[Claim]] = {}

    def claims_of(self, cid: int) -> list[Claim]:
        if cid not in self._claims:
            c = self.s.chunks[cid]
            ok = c.kind == "para" and self.s.pages[c.url].kind in CLAIM_PAGE_KINDS
            self._claims[cid] = extract_claims(cid, c.text) if ok else []
        return self._claims[cid]

    def resolve(self, claim: Claim, vid: int) -> list[str]:
        """Table fact keys the claim is about (all tied tiers), or [] when it names no product the tables have.
        The sentence's own product words decide first; its heading only when the sentence names none (one
        disclosure paragraph is often repeated under every product tab of a page)."""
        c = self.s.chunks[claim.cid]
        heading = c.ctx.split(" > ")[-1] if c.ctx else ""
        return self._resolve(claim, vid, _words(claim.sentence)) or \
            self._resolve(claim, vid, _words(f"{claim.sentence} {heading}"))

    def _resolve(self, claim: Claim, vid: int, near: set) -> list[str]:
        s = self.s
        c = s.chunks[claim.cid]
        info = s.pages[c.url]
        path = re.sub(r"^https?://[^/]+/(?:personal/|business/)?", "", c.url)
        far = _words(f"{c.ctx} {info.title} {re.sub(r'[/_.-]', ' ', path)}")   # adds weight, never decides
        scope = "business" if "business" in c.url.lower() else "personal"
        cands = []
        for key, win in s.winners.get(vid, {}).items():
            if not key.startswith(scope + " | ") or len(key) > 120 or "<" in key:
                continue
            kw = _words(_key_product(key))
            if scope == "personal" and "business" in kw:
                continue
            distinct = (kw & near) - GENERIC
            if not distinct and not (len(kw) >= 2 and kw <= near):
                continue
            vals = dict(row_values(s.chunks[win]))
            cols = [x for x in vals if x.split("-")[0] == claim.col and not x.startswith("hours:")]
            if not cols:
                continue
            if claim.terms:
                kt = {int(t) for t in KEY_TERM_RE.findall(key)}
                if not kt & set(claim.terms):
                    continue        # a stated term must be the key's term
            if claim.amount is not None:
                tier = _tier(key)
                if tier and not tier[0] <= claim.amount <= tier[1]:
                    continue
            k_intro = bool(_INTRO_KEY_RE.search(key)) or any(x.endswith("-intro") for x in cols)
            cands.append((2 * len(kw & near) + len((kw & far) - near), k_intro, key))
        # intro vs standard: a standard claim never resolves to an intro key; an intro claim prefers intro keys
        if not claim.intro:
            cands = [x for x in cands if not x[1]]
        elif any(x[1] for x in cands):
            cands = [x for x in cands if x[1]]
        if not cands:
            return []
        best = max(n for n, _, _ in cands)
        return sorted(k for n, _, k in cands if n == best)

    def table_values(self, keys: list[str], col: str, vid: int) -> dict[str, tuple]:
        s = self.s
        out = {}
        for k in keys:
            win = s.winners.get(vid, {}).get(k)
            if win is None:
                continue
            for x, nums in row_values(s.chunks[win]):
                if x.split("-")[0] == col:
                    out[k] = nums
        return out

    @staticmethod
    def agrees(claim: Claim, tvals: dict) -> bool:
        # a single value agrees with a table range when it is one of its ends ("12.50% to 19.50%" read in parts)
        return any(claim.value == v or (len(claim.value) == 1 and claim.value[0] in v) for v in tvals.values())

    def disagreements(self, vid: int):
        """(claim, resolved keys, table values) for every claim visible in view `vid` that no tier of its product
        carries, on a page that is crawled in that view."""
        s = self.s
        bit = 1 << vid
        out = []
        for cid, c in enumerate(s.chunks):
            if c.kind != "para" or not s.chunk_view[cid] & bit:
                continue
            for cl in self.claims_of(cid):
                keys = self.resolve(cl, vid)
                if not keys:
                    continue
                tv = self.table_values(keys, cl.col, vid)
                if tv and not self.agrees(cl, tv):
                    out.append((cl, keys, tv))
        return out
