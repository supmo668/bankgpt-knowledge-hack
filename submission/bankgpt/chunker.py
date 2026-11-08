"""Split one crawled page (markdown) into retrievable chunks: table rows, label groups, paragraphs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from urllib.parse import urlparse

from .text import clean_inline, parse_date_line

EFFECTIVE_RE = re.compile(r"effective (?:as of )?(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
BOLD_LABEL_RE = re.compile(r"^\*\*([^*]{1,40})\*\*:?$")
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
    tcount = 0
    pending_table: list[Chunk] = []

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
        chunks.append(Chunk(url, txt, head_ctx(), "para", links=links))
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
            tcount += 1
            tkey = f"{url}#t{tcount}"
            pending_table = []
            ctx = " > ".join(x for x in (head_ctx(), caption) if x)
            while i < n and lines[i].strip().startswith("|"):
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
            continue
        if not s:
            flush_para()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para()
            flush_group()
            lvl = len(m.group(1))
            heads = {k: v for k, v in heads.items() if k < lvl}
            heads[lvl] = clean_inline(m.group(2))[0]
            caption = ""
            i += 1
            continue
        if pending_table:
            em = EFFECTIVE_RE.search(s)
            if em:
                for c in pending_table:
                    c.effective = c.effective or em.group(1)
        # short plain line right before a table acts as its caption
        cl = clean_inline(s)[0]
        if 0 < len(cl) <= 60 and not cl.endswith((".", ":", "?", "!")) and not s.startswith(("-", "*", "[", "!")):
            caption = cl
        if s.startswith(("- ", "* ", "+ ")) and not para:
            para.append(s[2:])
        else:
            para.append(s)
        i += 1
    flush_para()
    flush_group()
    return chunks
