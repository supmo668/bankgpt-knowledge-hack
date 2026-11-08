"""Versioned fact store: every chunk of every snapshot, deduplicated, with a BM25 inverted index.

A chunk is identified by (url, context, text). Each chunk carries a bitmask of the snapshots it
appeared in, so `as_of` selection is a bit test and a newer snapshot never overwrites an older one.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import Optional

from .chunker import Chunk, PageInfo, chunk_page, page_info
from .text import tokens

K1, B = 1.2, 0.5


class Store:
    def __init__(self) -> None:
        self.snapshots: list[tuple[str, str]] = []          # (snapshot_id, taken_on), ingest order
        self.chunks: list[Chunk] = []
        self.chunk_mask: list[int] = []
        self.chunk_len: list[int] = []
        self.chunk_tf: list[dict] = []
        self.key2id: dict[tuple, int] = {}
        self.pages: dict[str, PageInfo] = {}                 # url -> latest PageInfo
        self.page_snaps: dict[str, int] = defaultdict(int)   # url -> snapshot mask
        self.page_hash: dict[tuple[str, int], int] = {}      # (url, snap) -> content hash
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.table_rows: dict[str, list[int]] = defaultdict(list)
        self._idf: dict[str, float] = {}
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
            self.page_hash[(p.url, si)] = hash(p.markdown)
            for c in chunk_page(p.url, info.title, p.markdown):
                key = (c.url, c.ctx, c.text)
                cid = self.key2id.get(key)
                if cid is None:
                    cid = len(self.chunks)
                    self.key2id[key] = cid
                    self.chunks.append(c)
                    self.chunk_mask.append(0)
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
                self.chunk_mask[cid] |= bit
        self._finalize()
        return si

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
                if not self.chunk_mask[cid] & bit:
                    continue
                f = self.chunk_tf[cid][t]
                dl = self.chunk_len[cid]
                scores[cid] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self._avglen))
        return scores

    def rows_of(self, table: str, si: int) -> list[int]:
        bit = 1 << si
        return [cid for cid in self.table_rows.get(table, []) if self.chunk_mask[cid] & bit]

    def recency(self, info: PageInfo, ref: date) -> float:
        """1.0 for undated live pages; dated pages decay over ~6 years before the snapshot date."""
        if info.pub_date is None:
            return 1.0
        age = max(0, (ref - info.pub_date).days)
        return max(0.0, 1.0 - age / (365 * 6))
