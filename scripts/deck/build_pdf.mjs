#!/usr/bin/env node
// Export docs/deck/deck.md to docs/deck/deck.pdf: one 16:9 page per slide (slides split on a `---` line).
//
//   cd scripts/deck && npm ci && node build_pdf.mjs
//
// Pure JS (pdfkit + svg-to-pdfkit + marked, DejaVu fonts): no browser and no system libraries. It renders the
// markdown subset the deck uses: h1/h2, paragraphs with bold/italic/code/links, lists, one SVG figure per slide,
// simple tables and code blocks. Content that doesn't fit its slide fails the build instead of being clipped.
import fs from 'node:fs';
import crypto from 'node:crypto';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { marked } from 'marked';
import PDFDocument from 'pdfkit';
import SVGtoPDF from 'svg-to-pdfkit';

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const DECK_DIR = path.resolve(HERE, '../../docs/deck');
const SRC = path.join(DECK_DIR, 'deck.md');
const OUT = path.join(DECK_DIR, 'deck.pdf');
const TTF = path.join(path.dirname(require.resolve('dejavu-fonts-ttf/package.json')), 'ttf');

const W = 960, H = 540, MX = 48, TOP = 40, BOTTOM = 500, CW = W - 2 * MX;
const INK = '#0b0b0b', INK2 = '#52514e', MUTED = '#8a8984', ACCENT = '#2a78d6', RULE = '#e4e3df', CODEBG = '#f2f1ee';
const FOOTER = 'bank-gpt · HACA-8 L2 proof of work';

// Fingerprint of every input (deck.md + figures/*.svg, sorted by name): tests/test_deck.py recomputes it, so a
// PDF built from an older deck or older figures fails the test, not just one with the wrong page count.
const FIG_DIR = path.join(DECK_DIR, 'figures');
const SOURCE_HASH = (() => {
  const h = crypto.createHash('sha256');
  h.update(fs.readFileSync(SRC));
  for (const f of fs.readdirSync(FIG_DIR).filter((n) => n.endsWith('.svg')).sort()) {
    h.update(`\0${f}\0`);
    h.update(fs.readFileSync(path.join(FIG_DIR, f)));
  }
  return h.digest('hex');
})();

const doc = new PDFDocument({ size: [W, H], margin: 0, autoFirstPage: false, compress: true,
  info: { Title: 'bank-gpt: L2 proof of work', Author: 'bank-gpt', Subject: 'Knowledge Hackathon', Keywords: `source-sha256-${SOURCE_HASH}`,
          CreationDate: new Date(Date.UTC(2026, 10, 8)) } });  // fixed date: rebuilds stay byte-stable
doc.registerFont('R', path.join(TTF, 'DejaVuSans.ttf'));
doc.registerFont('B', path.join(TTF, 'DejaVuSans-Bold.ttf'));
doc.registerFont('I', path.join(TTF, 'DejaVuSans-Oblique.ttf'));
doc.registerFont('M', path.join(TTF, 'DejaVuSansMono.ttf'));

// ── inline text ──────────────────────────────────────────────────────────
function segments(tokens, font = 'R') {
  const out = [];
  for (const t of tokens || []) {
    if (t.type === 'strong') out.push(...segments(t.tokens, 'B'));
    else if (t.type === 'em') out.push(...segments(t.tokens, font === 'B' ? 'B' : 'I'));
    else if (t.type === 'codespan') out.push({ text: decode(t.text), font: 'M' });
    else if (t.type === 'link') out.push(...segments(t.tokens, font));
    else if (t.tokens) out.push(...segments(t.tokens, font));
    else if (t.type === 'br') out.push({ text: '\n', font });
    else out.push({ text: decode(t.text ?? t.raw ?? ''), font });
  }
  return out;
}
const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  .replace(/&amp;/g, '&');

function measure(segs, size, width) {
  doc.font('R').fontSize(size);
  return doc.heightOfString(segs.map((s) => s.text).join(''), { width, lineGap: size * 0.3 });
}

function inline(segs, x, y, size, width, color = INK) {
  doc.fillColor(color).fontSize(size);
  segs.forEach((s, i) => {
    doc.font(s.font);
    const opts = { width, lineGap: size * 0.3, continued: i < segs.length - 1 };
    if (i === 0) doc.text(s.text, x, y, opts); else doc.text(s.text, opts);
  });
  return doc.y;
}

// ── blocks ───────────────────────────────────────────────────────────────
const isImage = (t) => t.type === 'paragraph' && t.tokens?.length === 1 && t.tokens[0].type === 'image';

function blockHeight(t, title) {
  const size = title ? 15 : 14;
  if (t.type === 'paragraph') return measure(segments(t.tokens), size, CW) + 10;
  if (t.type === 'list') return t.items.reduce((h, it) => h + measure(segments(it.tokens), 14, CW - 24) + 6, 4);
  if (t.type === 'table') return 22 * (t.rows.length + 1) + 10;
  if (t.type === 'code') return 14 * t.text.split('\n').length + 22;
  return 0;
}

function drawBlock(t, y) {
  if (t.type === 'paragraph') return inline(segments(t.tokens), MX, y, 14, CW, INK2) + 10;
  if (t.type === 'list') {
    let cy = y;
    t.items.forEach((it, i) => {
      const mark = t.ordered ? `${(t.start || 1) + i}.` : '•';
      doc.font(t.ordered ? 'B' : 'R').fontSize(14).fillColor(ACCENT).text(mark, MX, cy, { width: 22 });
      cy = inline(segments(it.tokens), MX + 24, cy, 14, CW - 24, INK) + 6;
    });
    return cy + 4;
  }
  if (t.type === 'table') {
    const cols = t.header.length;
    const widths = colWidths(t, cols);
    const row = (cells, cy, font, color) => {
      let cx = MX;
      cells.forEach((c, j) => {
        doc.font(font).fontSize(11).fillColor(color)
          .text(segments(c.tokens).map((s) => s.text).join(''), cx + 4, cy + 5, { width: widths[j] - 8, lineBreak: false,
            ellipsis: true });
        cx += widths[j];
      });
    };
    let cy = y;
    row(t.header, cy, 'B', INK);
    cy += 22;
    doc.moveTo(MX, cy).lineTo(MX + CW, cy).lineWidth(1).strokeColor(INK2).stroke();
    for (const r of t.rows) {
      row(r, cy, 'R', INK2);
      cy += 22;
      doc.moveTo(MX, cy).lineTo(MX + CW, cy).lineWidth(0.5).strokeColor(RULE).stroke();
    }
    return cy + 10;
  }
  if (t.type === 'code') {
    const lines = t.text.split('\n');
    const h = 14 * lines.length + 16;
    doc.roundedRect(MX, y, CW, h, 4).fill(CODEBG);
    doc.font('M').fontSize(10.5).fillColor(INK);
    lines.forEach((ln, i) => doc.text(ln, MX + 12, y + 8 + 14 * i, { width: CW - 24, lineBreak: false }));
    return y + h + 6;
  }
  return y;
}

function colWidths(t, cols) {
  const width = (cell, font) => doc.font(font).fontSize(11)
    .widthOfString(segments(cell.tokens).map((s) => s.text).join('')) + 12;
  const natural = [...Array(cols).keys()].map((j) => Math.max(width(t.header[j], 'B'),
    ...t.rows.map((r) => width(r[j], 'R'))));
  const total = natural.reduce((a, b) => a + b, 0);
  return natural.map((w) => (w * CW) / Math.max(total, CW));
}

function drawImage(t, y, maxH) {
  const href = t.tokens[0].href;
  // Only figures/*.svg may be embedded: a stray `![](../../x)` must not pull an arbitrary local file into the PDF.
  const file = path.resolve(DECK_DIR, href);
  if (path.dirname(file) !== FIG_DIR || !file.endsWith('.svg')) throw new Error(`image outside figures/*.svg: ${href}`);
  // Drop the root's fixed width/height so svg-to-pdfkit scales the viewBox into the box it is given.
  const svg = fs.readFileSync(file, 'utf8')
    .replace(/<svg([^>]*?) width="[\d.]+" height="[\d.]+"/, '<svg$1');
  const vb = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
  const [vw, vh] = vb ? [Number(vb[1]), Number(vb[2])] : [960, 540];
  const scale = Math.min(CW / vw, maxH / vh);
  const w = vw * scale, h = vh * scale;
  SVGtoPDF(doc, svg, MX + (CW - w) / 2, y, {
    width: w, height: h, assumePt: true, preserveAspectRatio: 'xMidYMid meet',
    fontCallback: (family, bold, italic) => (family.includes('mono') ? 'M' : bold ? 'B' : italic ? 'I' : 'R'),
  });
  return y + h + 8;
}

// ── slides ───────────────────────────────────────────────────────────────
function slide(md, n, total) {
  const tokens = marked.lexer(md).filter((t) => !['space', 'html', 'hr'].includes(t.type));
  doc.addPage();
  doc.rect(0, 0, W, H).fill('#fcfcfb');
  let y = TOP;
  const head = tokens[0]?.type === 'heading' ? tokens.shift() : null;
  const cover = head?.depth === 1;
  if (head) {
    if (cover) {
      doc.rect(MX, 150, 6, 64).fill(ACCENT);
      y = inline(segments(head.tokens), MX + 22, 160, 26, CW - 22, INK) + 36;
    } else {
      y = inline(segments(head.tokens).map((s) => ({ ...s, font: s.font === 'R' ? 'B' : s.font })), MX, TOP, 24,
        CW, INK) + 16;
    }
  }
  const rest = tokens.filter((t) => !isImage(t)).reduce((h, t) => h + blockHeight(t), 0);
  const imageH = BOTTOM - y - rest - 8;
  for (const t of tokens) {
    y = isImage(t) ? drawImage(t, y, imageH) : drawBlock(t, y);
    if (y > BOTTOM + 6) throw new Error(`slide ${n} overflows (${Math.round(y)} > ${BOTTOM}): trim it`);
  }
  doc.moveTo(MX, H - 26).lineTo(W - MX, H - 26).lineWidth(0.5).strokeColor(RULE).stroke();
  doc.font('R').fontSize(9).fillColor(MUTED).text(FOOTER, MX, H - 20, { lineBreak: false });
  doc.text(`${n} / ${total}`, W - MX - 60, H - 20, { width: 60, align: 'right', lineBreak: false });
}

const text = fs.readFileSync(SRC, 'utf8').replace(/<!--[\s\S]*?-->/g, '');
const slides = text.split(/^---\s*$/m).map((s) => s.trim()).filter(Boolean);
const out = fs.createWriteStream(OUT);
doc.pipe(out);
slides.forEach((s, i) => slide(s, i + 1, slides.length));
doc.end();
out.on('finish', () => console.log(`wrote ${path.relative(process.cwd(), OUT)} (${slides.length} slides)`));
