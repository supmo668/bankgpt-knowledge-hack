"""Text normalisation shared by ingest and query: cleaning markdown, tokens, numbers, ranges."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"])}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
BARE_URL_RE = re.compile(r"https?://\S+")
FOOTNOTE_RE = re.compile(r"(\d%|\bAP[RY]|\(AP[RY]\))(\d)\b")
ESC_NL_RE = re.compile(r"(?:\\+[rn])+")
WS_RE = re.compile(r"\s+")


def clean_inline(s: str) -> tuple[str, list[str]]:
    """Strip images, unwrap links (returning their targets), drop markdown emphasis + footnote digits."""
    s = ESC_NL_RE.sub(" ", s).replace("\\", "")
    s = IMG_RE.sub("", s)
    links = [m.group(2) for m in LINK_RE.finditer(s)]
    s = LINK_RE.sub(lambda m: m.group(1), s)
    s = s.replace("**", "").replace("__", "")
    s = FOOTNOTE_RE.sub(lambda m: m.group(1), s)
    return WS_RE.sub(" ", s).strip(), links


NUMWORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
            "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15",
            "eighteen": "18", "twenty": "20", "thirty": "30", "forty": "40", "sixty": "60"}

STOP = set("""a an the of and or to in on for is are was were be been do does did can could would should
what whats which who whom whose when where why how i im me my we our you your it its this that these those there
at by with from as about into than then so if any all some please tell give get me know want like much many
dupaco dupacos credit union s current currently now right today still have has had will just also more most
""".split())

# query-side expansions: (pattern on normalised question) -> extra tokens
SYNONYMS = [
    (r"\bcds?\b|\bcertificates? of deposit\b|\bshare certificates?\b", "certificate"),
    (r"\bmortgages?\b", "home loan mortgage conventional fixed"),
    (r"\bheloc\b", "home equity line credit"),
    (r"\binterest rate\b", "rate"),
    (r"\bopening hours\b|\bopen\b.*\bhours\b|\bbusiness hours\b", "lobby hours"),
    (r"\blocator\b|\bfind (a )?branch\b", "location branch atm"),
    (r"\bcar\b|\bvehicle\b", "auto"),
    (r"\bsavings? account\b", "savings"),
    (r"\bjoin\b|\bbecome a member\b|\bmembership\b", "join member"),
    (r"\bphone number\b|\bcall\b", "phone"),
    (r"\bdebit card\b", "debit card"),
    (r"\bmobile wallet\b|\bapple pay\b|\bgoogle pay\b", "mobile wallet pay"),
]

TOKEN_RE = re.compile(r"\d+/\d+|[a-z0-9]+(?:\$[a-z]+)?")


def stem(t: str) -> str:
    if t.isdigit() or len(t) <= 3:
        return t
    for suf in ("ies", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            if suf == "ies":
                return t[:-3] + "y"
            if suf == "es" and t[-3] in "sxz":
                return t[:-2]
            if suf == "s" and not t.endswith("ss"):
                return t[:-1]
    return t


def tokens(text: str, drop_stop: bool = True) -> list[str]:
    s = text.lower().replace("–", "-").replace("—", "-").replace("’", "'")
    s = re.sub(r"(\d),(\d{3})", r"\1\2", s)
    out = []
    for t in TOKEN_RE.findall(s):
        t = NUMWORDS.get(t, t)
        if drop_stop and t in STOP:
            continue
        out.append(stem(t))
    return out


def expand_query(q: str) -> str:
    low = q.lower()
    extra = [rep for pat, rep in SYNONYMS if re.search(pat, low)]
    return q + (" " + " ".join(extra) if extra else "")


# ---------------------------------------------------------------- numbers & ranges
MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
YEAR_RANGE_RE = re.compile(r"\b(20\d\d|19\d\d)\s?[-–]\s?(\d{2,4})\b")


def money_values(s: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) for m in MONEY_RE.finditer(s)]


def pct_values(s: str) -> list[str]:
    return [m.group(1) for m in PCT_RE.finditer(s)]


def amount_range(s: str):
    """A balance / loan-amount tier stated in text -> (lo, hi) or None."""
    s2 = s.replace(",", "")
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s?[-–]\s?\$\s?(\d+(?:\.\d+)?)", s2)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s?(?:\+|and (?:up|over|above)|or more)", s2)
    if m:
        return float(m.group(1)), float("inf")
    m = re.search(r"\$\s?(\d+(?:\.\d+)?)\s?(?:or less|and (?:under|below))", s2)
    if m:
        return 0.0, float(m.group(1))
    return None


def year_range(s: str):
    m = YEAR_RANGE_RE.search(s)
    if m:
        lo, hi = m.group(1), m.group(2)
        hi = int(lo[:4 - len(hi)] + hi) if len(hi) < 4 else int(hi)
        return int(lo), hi
    m = re.search(r"\b(19\d\d|20\d\d) model year \(and older\)|\b(19\d\d|20\d\d) (?:model year )?(?:and|or) older", s, re.I)
    if m:
        return 1900, int(m.group(1) or m.group(2))
    m = re.search(r"\b(19\d\d|20\d\d) model year\b", s, re.I)
    if m:
        y = int(m.group(1))
        return y, y
    return None


DATE_LINE_RE = re.compile(
    r"^(?:published\s*:?\s*)?(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.? (\d{1,2}), (\d{4})$", re.I)


def parse_date_line(line: str):
    m = DATE_LINE_RE.match(line.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1).lower()[:3] if m.group(1).lower() != "sept" else "sept"],
                    int(m.group(2)))
    except (ValueError, KeyError):
        return None


ASOF_TEXT_RE = re.compile(
    r"\b(?:as of|in|during|on|back in)\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?:(\d{1,2}),?\s+)?(20\d\d)\b", re.I)


def as_of_from_text(q: str):
    """'as of May 2026' / 'in June 2026' -> ISO date at the end of that month (or the given day)."""
    m = ASOF_TEXT_RE.search(q)
    if not m:
        return None
    mon = MONTHS[m.group(1).lower()[:3] if m.group(1).lower() != "sept" else "sept"]
    yr = int(m.group(3))
    if m.group(2):
        d = int(m.group(2))
    else:
        nxt = date(yr + (mon == 12), mon % 12 + 1, 1)
        d = (nxt - date.resolution).day
    try:
        return date(yr, mon, d).isoformat()
    except ValueError:
        return None
