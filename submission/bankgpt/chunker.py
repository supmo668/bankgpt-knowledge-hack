"""Split one crawled page (markdown) into retrievable chunks: table rows, label groups, paragraphs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from .text import clean_inline, offer_end, parse_date_line, year_range

EFFECTIVE_RE = re.compile(r"effective (?:as of )?(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
BOLD_LABEL_RE = re.compile(r"^\*\*([^*]{1,40})\*\*:?$")
GENERIC_HEADER_RE = re.compile(r"(?i)^(term|terms?|account( type)?|certificate|program|card type|loan (program|amount|type)|"
                               r"ira type|privileges|product|type|balance( tier)?|rate|apr|apy|tier|amount|col\d*|"
                               r"model year|vehicle|description|fee|service|item|)$")
KV_RE = re.compile(r"^([^:]{3,80}?):\s*(\$?\s?\d[\d,]*(?:\.\d+)?\s?%?(?:\s*(?:APY|APR))?)\s*(?:\([^)]*\))?\.?$", re.I)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
NOISE_LINE_RE = re.compile(r"^(\* \* \*|---+|Loading locations\.\.\.|Go|Cancel|Continue.*Cancel|\d+ min read)$", re.I)


@dataclass
class Chunk:
    url: str
    text: str                 # display text (clean)
    ctx: str                  # heading path / table caption / page title
    kind: str                 # row | group | para
    table: Optional[str] = None      # table key (url#n) for rows
    cells: dict = field(default_factory=dict)
    effective: Optional[str] = None  # "Rates are effective as of ..." attached to a table
    links: list = field(default_factory=list)
    key_alias: dict = field(default_factory=dict)  # qualifier cell -> canonical form (model-year tiers)
    ends: Optional[str] = None       # ISO date an offer says it ended / ends ("offer ended 2025-09-30")


@dataclass
class PageInfo:
    url: str
    path: str
    title: str
    kind: str
    authority: float
    pub_date: Optional[date]
    fetched_at: str


def page_kind(url: str) -> tuple[str, float]:
    """Source-authority ranking (see NOTES.md, 'Currency rules')."""
    p = urlparse(url)
    path = (p.path.rstrip("/") or "/").lower()
    q = p.query
    if "privacy-policy" in path and q:
        return "junk", 0.05
    if path in ("/rates", "/business-rates"):
        return "rate_sheet", 1.0
    if path.startswith("/offer-expired"):
        return "expired", 0.1
    if path.startswith("/calculators") or path.startswith("/business-calculators") or (
            path.startswith("/business/resources/") and "calculator" in path):
        return "calculator", 0.25
    if "/tag/" in path or "/author/" in path or path.startswith("/resources") or path == "/business/resources":
        return "index", 0.2
    if path.startswith("/newsroom"):
        return "newsroom", 0.6
    if path.startswith("/learn") or re.match(r"^/20\d\d/", path):
        return "blog", 0.45
    if path.startswith("/business/resources"):
        return "resource", 0.4
    if path.startswith("/hubfs"):
        return "document", 0.6
    if path.endswith("-lp"):
        return "landing", 0.7
    if path == "/":
        return "home", 0.85
    return "product", 0.9


def page_info(url: str, title: str, markdown: str, fetched_at: str) -> PageInfo:
    kind, auth = page_kind(url)
    pub = None
    if kind in ("newsroom", "blog", "resource"):
        for line in markdown.split("\n")[:15]:
            pub = parse_date_line(line)
            if pub:
                break
    p = urlparse(url)
    return PageInfo(url=url, path=(p.path.rstrip("/") or "/").lower(), title=title.split("|")[0].strip(),
                    kind=kind, authority=auth, pub_date=pub, fetched_at=fetched_at)


def _alias_year_tiers(rows: list[Chunk]) -> None:
    """'2024-27 Model Year' on the rate sheet and '2023-26 Model Year' on a stale page are the same fact:
    the newest model-year tier. Alias each year-range cell to its rank in the table (0 = newest)."""
    found = []
    for r in rows:
        first = next(iter(r.cells.values()), "")
        yr = year_range(first)
        if yr and re.search(r"(?i)model|year|older", first):
            found.append((yr, first, r))
    ranges = sorted({f[0] for f in found}, key=lambda x: (-x[1], -x[0]))
    if len(ranges) < 2:
        return
    for yr, first, r in found:
        r.key_alias[first] = f"model-year tier {ranges.index(yr)}"


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def chunk_page(url: str, title: str, markdown: str) -> list[Chunk]:
    lines = markdown.replace("\r", "").split("\n")
    heads: dict[int, str] = {}
    caption = ""
    chunks: list[Chunk] = []
    para: list[str] = []
    group: Optional[list] = None   # [label, lines, ctx]
    last_para_text = ""
    section = ""
    prev_short = False
    pending_table: list[Chunk] = []

    seen_tables: dict[str, int] = {}

    def ctx_now() -> str:
        return " > ".join(x for x in (head_ctx(), section, caption) if x)

    def head_ctx() -> str:
        return " > ".join(heads[k] for k in sorted(heads))

    def flush_group():
        nonlocal group
        if group and group[1]:
            body = "; ".join(group[1])
            chunks.append(Chunk(url, f"{group[0]}: {body}", group[2], "group"))
        group = None

    def flush_para():
        nonlocal para, last_para_text, group
        if not para:
            return
        raw = " ".join(para)
        para = []
        txt, links = clean_inline(raw)
        if not txt or NOISE_LINE_RE.match(txt):
            return
        if group is not None:
            if len(txt) <= 120 and not BOLD_LABEL_RE.match(raw.strip()):
                group[1].append(txt)
                return
            flush_group()
        m = BOLD_LABEL_RE.match(raw.strip())
        if m:
            group = [m.group(1).strip(), [], (head_ctx() + " | " + last_para_text[-160:]).strip(" |")]
            return
        ctx = " > ".join(x for x in (head_ctx(), section, caption) if x and x != txt)
        ends = offer_end(txt)
        kv = KV_RE.match(txt)
        if kv and len(txt) <= 160:
            # "6-month certificate APY: 3.50%" / "Routing Number: 273974581" is a one-row fact table
            chunks.append(Chunk(url, txt, ctx, "row", table=f"{url}#kv:{txt[:80]}",
                                cells={"Item": kv.group(1).strip(), "Value": kv.group(2).strip()}, ends=ends, links=links))
            last_para_text = txt
            return
        chunks.append(Chunk(url, txt, ctx, "para", links=links, ends=ends))
        if len(txt) > 350:
            # long paragraphs are also indexed sentence by sentence (precise hits, short answers)
            sents = SENT_SPLIT_RE.split(txt)
            for snt in sents:
                if len(snt) >= 25:
                    chunks.append(Chunk(url, snt, ctx, "sent", ends=offer_end(snt)))
        last_para_text = txt

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip()
        s = line.strip()
        if s.startswith("|") and i + 1 < n and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1]):
            flush_para()
            flush_group()
            header = [clean_inline(c)[0] for c in _split_row(s)]
            i += 2
            data_header = None
            if any(re.search(r"\$\s?\d|\d\s?%", h) for h in header[1:]):
                # the "header" is really the first data row (fee schedules exported from PDF)
                data_header = header
                hdr = "Fee" if any("$" in h for h in header[1:]) else "Value"
                header = ["Item"] + [hdr if k == 1 else f"{hdr} {k}" for k in range(1, len(header))]
            # content-based table key (context + header + occurrence), stable when tables are added above
            sig = f"{ctx_now()}|{'|'.join(header)}"
            seen_tables[sig] = seen_tables.get(sig, 0) + 1
            tkey = f"{url}#{sig}#{seen_tables[sig]}"
            pending_table = []
            ctx = " > ".join(x for x in (head_ctx(), section, caption) if x)
            h0 = header[0].strip(" *") if header else ""
            if h0 and not GENERIC_HEADER_RE.match(h0) and h0.lower() not in ctx.lower():
                ctx = f"{ctx} > {h0}" if ctx else h0   # header names the product ("Business Platinum Visa | APR")
            pending_rows = [data_header] if data_header else []
            while pending_rows or (i < n and lines[i].strip().startswith("|")):
                if pending_rows:
                    cells = pending_rows.pop()
                else:
                    cells = [clean_inline(c)[0] for c in _split_row(lines[i])]
                    i += 1
                if not any(cells):
                    continue
                pairs = {header[k] if k < len(header) and header[k] else f"col{k}": v
                         for k, v in enumerate(cells) if v and v not in ("—", "-")}
                first = cells[0]
                rest = ", ".join(f"{k} {v}" for k, v in list(pairs.items())[1:])
                txt = f"{first}: {rest}" if rest else first
                c = Chunk(url, txt, ctx, "row", table=tkey, cells=pairs)
                pending_table.append(c)
                chunks.append(c)
            _alias_year_tiers(pending_table)
            prev_short = False   # the next short line captions the next table; it is not a section
            continue
        if s.startswith("|") and s.count("|") >= 2 and not (i + 1 < n and re.match(r"^\s*\|?\s*:?-{3,}", lines[i + 1])):
            # a pipe table with no header separator (PDF fee schedules): label | value rows,
            # single-cell rows are sub-captions
            flush_para()
            flush_group()
            sig = f"{ctx_now()}|headerless|{s[:40]}"
            seen_tables[sig] = seen_tables.get(sig, 0) + 1
            tkey = f"{url}#{sig}#{seen_tables[sig]}"
            sub = ""
            while i < n and lines[i].strip().startswith("|"):
                raw_cells = _split_row(lines[i])
                i += 1
                if all(re.fullmatch(r":?-{2,}:?", c) or not c for c in raw_cells):
                    continue
                cells = [clean_inline(c)[0] for c in raw_cells if c.strip()]
                if len(cells) == 1:
                    sub = cells[0]
                    continue
                hdr = "Fee" if any("$" in c for c in cells[1:]) else "Value"
                pairs = {"Item": cells[0], **{(hdr if k == 1 else f"{hdr} {k}"): v for k, v in enumerate(cells[1:], 1)}}
                txt = f"{cells[0]}: {', '.join(cells[1:])}"
                ctx = " > ".join(x for x in (head_ctx(), section, caption, sub) if x)
                chunks.append(Chunk(url, txt, ctx, "row", table=tkey, cells=pairs))
            prev_short = False
            continue
        if not s:
            flush_para()
            i += 1
            continue
        cl = clean_inline(s)[0]
        is_short = 0 < len(cl) <= 60 and not cl.endswith((".", ":", "?", "!")) and not s.startswith(("-", "*", "[", "!")) \
            and not re.search(r"\d\s?%|\$\s?\d", cl) and not KV_RE.match(cl)
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para()
            flush_group()
            lvl = len(m.group(1))
            heads = {k: v for k, v in heads.items() if k < lvl}
            heads[lvl] = clean_inline(m.group(2))[0]
            caption = section = ""
            prev_short = False
            i += 1
            continue
        if pending_table:
            em = EFFECTIVE_RE.search(s)
            if em:
                for c in pending_table:
                    c.effective = c.effective or em.group(1)
        # short plain line right before a table acts as its caption; two in a row = section + caption
        if is_short:
            if prev_short and caption:
                section = caption
            caption = cl
        prev_short = is_short
        if s.startswith(("- ", "* ", "+ ")) and not para:
            para.append(s[2:])
        else:
            para.append(s)
        i += 1
    flush_para()
    flush_group()
    return chunks
