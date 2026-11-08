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

REFUSAL_TEXT = "I can't confirm that: it isn't listed on the website. Please contact us at 800-373-7600 for details."

RATE_INTENT_RE = re.compile(r"\b(rate|rates|apr|apy|dividend|yield|interest|fee|fees|cost|charge|price|how much)\b", re.I)
COUNT_INTENT_RE = re.compile(r"\bhow many\b", re.I)
LINK_INTENT_RE = re.compile(r"\b(link|url|website address|web address|web page|webpage)\b", re.I)
SUPER_HI_RE = re.compile(r"\b(highest|best|top|maximum|max|most)\b", re.I)
SUPER_LO_RE = re.compile(r"\b(lowest|minimum|cheapest|least)\b", re.I)
TERM_RE = re.compile(r"\b(\d{1,3})[\s-]*(month|mo|year|yr)s?\b", re.I)
MODEL_YEAR_RE = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
GENERIC_CAPS = {"what", "how", "when", "where", "which", "who", "why", "is", "are", "do", "does", "can", "could",
                "would", "i", "i'm", "im", "dupaco", "dupaco's", "the", "a", "an", "my", "your", "if", "please",
                "provide", "get", "tell", "give", "show", "list", "should", "will", "any", "all", "apy", "apr",
                "cd", "cds", "us", "visa", "october", "september", "november", "december", "january", "february",
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


def _short(s: str) -> str:
    for rx, rep in HEADER_SHORT:
        s = rx.sub(rep, s)
    return s


def _entities(q: str) -> list[str]:
    """Specific names the answer must mention: capitalised words mid-sentence, acronyms, 7/1-style codes."""
    out = []
    words = re.findall(r"[A-Za-z0-9$'/&-]+", q)
    for i, w in enumerate(words):
        lw = w.lower().strip("'s").rstrip("'")
        if lw in GENERIC_CAPS or len(lw) < 2:
            continue
        if re.fullmatch(r"\d+/\d+", w):
            out.append(w)
        elif i > 0 and w[0].isupper():
            out.append(w)
    return out


def _covers(store: Store, cid: int, ent: str) -> bool:
    c = store.chunks[cid]
    info = store.pages[c.url]
    hay = f"{info.title} {c.ctx} {c.text}".lower()
    e = ent.lower()
    if e in hay:
        return True
    et = tokens(e, drop_stop=False)
    ht = set(tokens(hay, drop_stop=False))
    return bool(et) and all(t in ht for t in et)


def _has_value(c: Chunk) -> bool:
    return bool(re.search(r"\d(?:\.\d+)?\s?%|\$\s?\d", c.text))


class Answerer:
    def __init__(self, store: Store) -> None:
        self.s = store

    # ------------------------------------------------------------------ ranking
    def rank(self, question: str, si: int, limit: int = 60) -> list[tuple[float, int]]:
        s = self.s
        qx = expand_query(question)
        qtoks = tokens(qx)
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
        rate_intent = bool(RATE_INTENT_RE.search(question))
        # only rerank the strongest lexical hits
        top = sorted(raw.items(), key=lambda kv: -kv[1])[:400]
        out = []
        for cid, sc in top:
            c = s.chunks[cid]
            info = s.pages[c.url]
            f = info.authority ** 0.8 * (0.8 + 0.4 * s.recency(info, ref))
            body = f"{c.ctx} {c.text}".lower()
            head = (c.text.split(":")[0] if c.kind == "row" else c.text).lower()
            for n, unit in terms:
                pat = rf"\b{n}[\s-]*{unit}"
                if re.search(pat, head if c.kind == "row" else body):
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
            if rate_intent and _has_value(c):
                f *= 1.3
            if c.kind == "row":
                f *= 1.1
            out.append((sc * f, cid))
        out.sort(key=lambda x: (-x[0], x[1]))
        # collapse duplicates of the same text (boilerplate repeated across pages): keep the best
        seen, res = set(), []
        for sc, cid in out:
            k = s.chunks[cid].text
            if k in seen:
                continue
            seen.add(k)
            res.append((sc, cid))
            if len(res) >= limit:
                break
        return res

    # ------------------------------------------------------------------ answer
    def answer(self, question: str, si: int) -> Result:
        s = self.s
        ranked = self.rank(question, si)
        if not ranked:
            return Result(REFUSAL_TEXT, confident=False)
        ents = _entities(question)
        rate_intent = bool(RATE_INTENT_RE.search(question)) and not COUNT_INTENT_RE.search(question)
        best = None
        top_score = ranked[0][0]
        for sc, cid in ranked[:40]:
            if sc < 0.25 * top_score:
                break
            c = s.chunks[cid]
            if ents and not all(_covers(s, cid, e) for e in ents):
                continue
            if rate_intent:
                info = s.pages[c.url]
                if not _has_value(c) or info.kind not in ("rate_sheet", "product", "landing", "home", "document"):
                    continue
            best = (sc, cid)
            break
        if best is None or best[0] < 3.0:
            return Result(REFUSAL_TEXT, confident=False)
        sc, cid = best
        c = s.chunks[cid]
        if LINK_INTENT_RE.search(question):
            return self._link_answer(question, ranked, cid, si)
        text, used = self._compose(question, c, cid, sc, ranked, si)
        return Result(text, sources=self._sources(used, ranked, si), confident=True, chunk_ids=used)

    def _compose(self, question: str, c: Chunk, cid: int, sc: float, ranked, si: int):
        s = self.s
        if c.kind == "row" and c.table:
            scores = dict((k, v) for v, k in ranked)
            rows = s.rows_of(c.table, si)
            if SUPER_HI_RE.search(question) or SUPER_LO_RE.search(question):
                def last_pct(r):
                    vals = re.findall(r"(\d+(?:\.\d+)?)\s?%", s.chunks[r].text)
                    return float(vals[-1]) if vals else None
                cand = [r for r in rows if last_pct(r) is not None]
                if cand:
                    pick = (max if SUPER_HI_RE.search(question) else min)(cand, key=lambda r: (last_pct(r), -r))
                    return self._render_rows([pick]), [pick]
            keep = [r for r in rows if scores.get(r, 0) >= 0.8 * sc]
            if cid not in keep:
                keep.insert(0, cid)
            keep = sorted(keep, key=lambda r: rows.index(r))[:6]
            return self._render_rows(keep), keep
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
        main = s.chunks[used[0]]
        out = [(main.url, sid, main.text[:200])]
        vals = set(re.findall(r"\d+(?:\.\d+)?%", main.text))
        if vals:
            for sc, cid in ranked[:40]:
                c = s.chunks[cid]
                info = s.pages[c.url]
                if c.url in {u for u, _, _ in out} or info.kind not in ("rate_sheet", "product", "landing", "home"):
                    continue
                if vals & set(re.findall(r"\d+(?:\.\d+)?%", c.text)):
                    out.append((c.url, sid, c.text[:200]))
                if len(out) >= 4:
                    break
        return out

    def _link_answer(self, question: str, ranked, cid: int, si: int) -> Result:
        s = self.s
        c = s.chunks[cid]
        url = c.url
        # an aggregate rate sheet is not the page for one product: prefer the product page it links to
        if s.pages[url].kind == "rate_sheet":
            for sc, other in ranked[:30]:
                oc = s.chunks[other]
                if s.pages[oc.url].kind in ("product", "landing"):
                    url = oc.url
                    break
        return Result(f"You can find it here: {url}", sources=[(url, s.snapshots[si][0], None)], chunk_ids=[cid])
