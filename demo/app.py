#!/usr/bin/env python3
"""Minimal local demo: question -> answer, sources, conflict note, with an `as_of` picker and the
conflicts table. Standard library only (no extra installs).

    python demo/app.py                 # http://127.0.0.1:8765
    python demo/app.py --port 9000 --snapshots snapshots

It reads the snapshots from your local snapshots/ folder (unpacked from snapshots_zip/, see NOTES.md);
nothing is fetched from the network and it binds to localhost only.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from run_eval import load_snapshots  # noqa: E402

import solution  # noqa: E402

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>bank-gpt knowledge demo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 :root { --fg:#1d2433; --muted:#5b6475; --line:#d9dee7; --accent:#1f5fbf; --bg:#f6f8fb; --warn:#8a5a00; }
 body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; color: var(--fg); background: var(--bg);
        margin: 0; }
 main { max-width: 880px; margin: 0 auto; padding: 24px 16px 64px; }
 h1 { font-size: 22px; margin: 0 0 4px; } p.lead { color: var(--muted); margin: 0 0 20px; }
 form { display: flex; gap: 8px; flex-wrap: wrap; }
 input[type=text] { flex: 1 1 420px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }
 select, button { padding: 10px 12px; border-radius: 8px; border: 1px solid var(--line); font: inherit; background: #fff; }
 button { background: var(--accent); color: #fff; border-color: var(--accent); cursor: pointer; }
 .card { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-top: 16px; }
 .meta { color: var(--muted); font-size: 13px; }
 .refused { border-left: 4px solid var(--warn); }
 .note { background: #fff8e6; border: 1px solid #f0d9a6; border-radius: 8px; padding: 8px 10px; margin-top: 10px; font-size: 13px; }
 ul.src { margin: 8px 0 0; padding-left: 18px; } ul.src li { margin: 2px 0; }
 table { border-collapse: collapse; width: 100%; font-size: 13px; }
 th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 6px 4px; vertical-align: top; }
 nav a { margin-right: 14px; color: var(--accent); }
</style></head>
<body><main>
<nav><a href="/">Ask</a><a href="/conflicts">Conflicts ({n_conflicts})</a></nav>
{body}
</main></body></html>"""


class Demo:
    def __init__(self, snapshots: Path) -> None:
        t = time.time()
        self.snaps = load_snapshots(snapshots)
        if not self.snaps:
            raise SystemExit(f"no snapshots under {snapshots}: unzip snapshots_zip/*.zip there first (NOTES.md)")
        self.ks = solution.build()
        for s in self.snaps:
            self.ks.ingest(s)
        self.conflicts = self.ks.conflicts()
        print(f"ingested {len(self.snaps)} snapshots in {time.time() - t:.1f}s", flush=True)

    def ask_page(self, q: str, as_of: str) -> str:
        opts = ['<option value="">latest (' + html.escape(self.snaps[-1].taken_on) + ")</option>"]
        for s in self.snaps:
            sel = " selected" if s.taken_on == as_of else ""
            opts.append(f'<option value="{s.taken_on}"{sel}>as of {s.taken_on} ({html.escape(s.snapshot_id)})</option>')
        body = ["<h1>What's true now?</h1>",
                '<p class="lead">Answers come from the crawled snapshots only: current value, right tier, a source, '
                "or an honest &ldquo;I don't know&rdquo;.</p>",
                '<form method="get" action="/"><input type="text" name="q" autofocus placeholder="e.g. What is the '
                '60-month auto loan rate for a 2025 car?" value="' + html.escape(q) + '"><select name="as_of">'
                + "".join(opts) + '</select><button type="submit">Ask</button></form>']
        if q:
            t = time.time()
            a = self.ks.answer(q, as_of=as_of or None)
            ms = (time.time() - t) * 1000
            cls = "card" + ("" if a.confident else " refused")
            body.append(f'<div class="{cls}"><div>{html.escape(a.text)}</div>')
            body.append(f'<div class="meta">{"confident" if a.confident else "declined"} · '
                        f'as_of={html.escape(as_of or "latest")} · {ms:.0f} ms</div>')
            if a.conflict_note:
                body.append(f'<div class="note"><b>Why this value:</b> {html.escape(a.conflict_note)}</div>')
            if a.sources:
                body.append('<ul class="src">' + "".join(
                    f'<li><a href="{html.escape(s.url)}" rel="noreferrer">{html.escape(s.url)}</a> '
                    f'<span class="meta">({html.escape(s.snapshot_id or "")})</span></li>' for s in a.sources) + "</ul>")
            body.append("</div>")
        return PAGE.replace("{n_conflicts}", str(len(self.conflicts))).replace("{body}", "".join(body))

    def conflicts_page(self) -> str:
        rows = []
        for c in self.conflicts:
            rows.append("<tr><td>" + html.escape(c.topic) + "</td><td>" + "<br>".join(html.escape(v) for v in c.values)
                        + "</td><td><b>" + html.escape(c.resolution or "") + "</b></td><td class=meta>"
                        + html.escape(c.reason or "") + "</td></tr>")
        body = ("<h1>Where the website contradicts itself</h1><p class=lead>Computed at ingest; resolution = the value "
                "the answer path gives today.</p><div class=card><table><tr><th>Topic</th><th>Values seen</th>"
                "<th>Resolution</th><th>Why</th></tr>" + "".join(rows) + "</table></div>")
        return PAGE.replace("{n_conflicts}", str(len(self.conflicts))).replace("{body}", body)


def make_handler(demo: Demo):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0][:500]
            as_of = (qs.get("as_of") or [""])[0][:10]
            if u.path == "/":
                self._send(200, demo.ask_page(q, as_of))
            elif u.path == "/conflicts":
                self._send(200, demo.conflicts_page())
            elif u.path == "/api/answer":
                a = demo.ks.answer(q, as_of=as_of or None)
                self._send(200, json.dumps(asdict(a), indent=2), "application/json")
            else:
                self._send(404, "not found", "text/plain")

        def log_message(self, fmt, *args):  # quiet
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, default=ROOT / "snapshots")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    demo = Demo(args.snapshots)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(demo))
    print(f"demo on http://127.0.0.1:{args.port}  (Ctrl-C to stop)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
