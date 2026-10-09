#!/usr/bin/env python3
"""Build the client handover package (Python standard library only).

    python3 tools/make_handover.py [--out DIR] [--no-screens]

Default output: dist/CLIENT_HANDOVER/ (git-ignored). The script
  - archives the committed source (git archive HEAD; the git-ignored .env never enters it),
  - writes the database schema and a sample SQL dump of a FRESH throw-away database (python3 app.py export-sql),
  - starts a throw-away server (random port, temporary DATA_DIR, random admin password that is never written to disk),
    seeds it with an About text, a category description and one example contact message, and takes full-page
    screenshots of every public page and admin screen at 390 px and 1366 px,
  - converts docs/handover/*.md to Arabic RTL HTML and prints them to A4 PDF (tools/render_pdf.cjs, Playwright),
  - copies the offline design-system page (with its fonts) and renders it to PDF,
  - adds FIGMA.txt, CHANGELOG.txt (git log) and .env.example.
Needs: git, Python 3.10+, Node with Playwright (NODE_PATH=$(npm root -g) is set automatically when missing).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDOVER_DOCS = ROOT / "docs" / "handover"
DESIGN_PAGE = ROOT / "docs" / "design" / "design-system.html"
FONTS_DIR = ROOT / "assets" / "fonts"
RENDERER = ROOT / "tools" / "render_pdf.cjs"
FIGMA_URL = "https://www.figma.com/design/LurpnArjhAjDr7oTmQKCOa"
STORE = "الفخامة للأقمشة والستائر"

# (markdown source, output path inside the package, footer title)
DOCS = [
    ("00-README.md", "00-README.pdf", "دليل حزمة التسليم"),
    ("01-User-Manual.md", "03-Documentation/User-Manual.pdf", "دليل المستخدم"),
    ("02-Admin-Manual.md", "03-Documentation/Admin-Manual.pdf", "دليل لوحة الإدارة"),
    ("03-Technical-Documentation.md", "03-Documentation/Technical-Documentation.pdf", "التوثيق التقني"),
    ("05-Deployment-Guide.md", "05-Deployment/Deployment-Guide.pdf", "دليل النشر"),
    ("06-Account-Handover.md", "06-Accounts/Account-Handover.pdf", "تسليم الحسابات والملكية"),
    ("07-Security-Checklist.md", "06-Accounts/Security-Checklist.pdf", "قائمة التحقق الأمني"),
    ("04-API-Documentation.md", "07-API/API-Documentation.pdf", "توثيق واجهة البرمجة (API)"),
    ("08-License-and-Scope.md", "08-License/License-and-Scope.pdf", "الترخيص ونطاق العمل (نموذج)"),
]
PUBLIC_SCREENS = [("home", "/"), ("products", "/products"), ("categories", "/categories"),
                  ("category-curtains", "/category/curtains"), ("product-wavy-03", "/product/wavy-03"),
                  ("about", "/about"), ("contact", "/contact"), ("faq", "/faq"), ("not-found-404", "/this-page-does-not-exist")]
ADMIN_SCREENS = [("dashboard", "overview"), ("products", "products"), ("orders", "orders"), ("messages", "messages"),
                 ("statistics", "analytics"), ("categories", "categories"), ("settings", "settings")]
WIDTHS = [390, 1366]
# Files that must never be delivered (secrets, local databases, signing keys).
FORBIDDEN = re.compile(r"(?:^|/)(?:\.env|store\.db(?:-wal|-shm)?|keystore\.properties|local\.properties|[^/]*\.jks|[^/]*\.keystore|[^/]*\.pid)$")
CATEGORY_DESCRIPTION = ("ستائر ويفي بطيّات متموجة تُفصَّل حسب مقاس النافذة. يتحدد السعر النهائي بحسب المقاس والخامة واللون، "
                        "ويؤكده المتجر معك قبل البدء.")
EXAMPLE_MESSAGE = {"name": "عميل تجريبي (مثال)", "phone": "0500000000", "email": "",
                   "message": "رسالة تجريبية لعرض صندوق الرسائل في حزمة التسليم: أرغب في الاستفسار عن ستائر ويفي لنافذة صالة.",
                   "page": "/contact", "consent": True, "website": ""}
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the throw-away server is on loopback


def log(message: str) -> None:
    print(message, flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


# ---------------------------------------------------------------------------------------------------------------------
# Markdown -> HTML (the subset the handover documents use: headings, paragraphs, lists, tables, code, quotes, links)
# ---------------------------------------------------------------------------------------------------------------------
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
STATUS = {"✓": "ok", "⚠": "warn", "✗": "bad"}


BARE_URL = re.compile(r"https?://[^\s<>()\[\]،]+[^\s<>()\[\].,،:;!?]")


def emphasis(text: str) -> str:
    return ITALIC.sub(r"<em>\1</em>", BOLD.sub(r"<strong>\1</strong>", text))


def inline(text: str) -> str:
    """Code spans, links and bare URLs are swapped for placeholders first, so nothing inside them is re-formatted."""
    held: list[str] = []

    def hold(markup: str) -> str:
        held.append(markup)
        return f"\x00{len(held) - 1}\x00"

    def code(m: re.Match) -> str:
        body = html.escape(m.group(2).strip())
        if len(body) > 14:  # long paths may wrap after a slash rather than mid-word
            body = re.sub(r"(?<=[/,=&?])(?=[^/,=&?])", "<wbr>", body)
        return hold(f'<code dir="ltr">{body}</code>')

    text = INLINE_CODE.sub(code, text)
    text = re.sub(r"\\([*_`|\[\]\\#])", lambda m: hold(html.escape(m.group(1))), text)  # \* and friends: literal

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        direction = ' dir="ltr"' if BARE_URL.fullmatch(label) else ""
        return hold(f'<a href="{html.escape(url, quote=True)}"{direction}>{emphasis(html.escape(label, quote=False))}</a>')

    text = LINK.sub(link, text)
    text = BARE_URL.sub(lambda m: hold(f'<a href="{html.escape(m.group(0), quote=True)}" dir="ltr">{html.escape(m.group(0))}</a>'), text)
    # a [placeholder] left over (not a link) is a field to fill in (the license template)
    text = re.sub(r"\[([^\[\]\n]*)\]", lambda m: hold(f'<span class="fill">[{emphasis(html.escape(m.group(1), quote=False))}]</span>'), text)
    text = emphasis(html.escape(text, quote=False))
    # ISO dates and times read left to right inside Arabic text (otherwise the bidi algorithm shows 09-10-2026)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:+]+)?\b", lambda m: f'<span dir="ltr">{m.group(0)}</span>', text)
    for symbol, cls in STATUS.items():
        text = text.replace(symbol, f'<span class="st {cls}">{symbol}</span>')
    while "\x00" in text:
        text = re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], text)
    return text


def slug(text: str, used: dict) -> str:
    """Short ASCII anchors (s1, s2, …): long Arabic ids become over-long named destinations in the PDF."""
    used["n"] = used.get("n", 0) + 1
    return f"s{used['n']}"


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, cur, in_code = [], "", False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            cur += "|"; i += 2; continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append(cur.strip()); cur = ""
        else:
            cur += ch
        i += 1
    cells.append(cur.strip())
    return cells


LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def render_list(lines: list[str]) -> str:
    """lines: a contiguous list block (items, nested items, continuation lines)."""
    first = LIST_ITEM.match(lines[0])
    indent = len(first.group(1))
    ordered = first.group(2)[0].isdigit()
    items: list[list[str]] = []
    for line in lines:
        m = LIST_ITEM.match(line)
        if m and len(m.group(1)) <= indent + 1:
            items.append([m.group(3)])
        elif items:
            items[-1].append(line)
    out = []
    for item in items:
        head, rest = item[0], item[1:]
        nested_start = next((i for i, ln in enumerate(rest) if LIST_ITEM.match(ln)), None)
        text_lines = [head] + [ln.strip() for ln in (rest if nested_start is None else rest[:nested_start]) if ln.strip()]
        task = re.match(r"\[([ xX])\]\s+", text_lines[0])  # - [ ] checklist item
        if task:
            text_lines[0] = text_lines[0][task.end():]
        body = inline(" ".join(text_lines))
        if task:
            body = f'<span class="box">{"☑" if task.group(1).strip() else "☐"}</span> ' + body
        if nested_start is not None:
            body += render_list([ln for ln in rest[nested_start:] if ln.strip()])
        out.append(f"<li>{body}</li>")
    start = ""
    if ordered:
        number = int(re.match(r"\d+", first.group(2)).group(0))
        start = f' start="{number}"' if number != 1 else ""
    tag = "ol" if ordered else "ul"
    return f"<{tag}{start}>" + "".join(out) + f"</{tag}>"


def md_to_html(text: str, headings: list | None = None) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    used: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1; continue
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i]); i += 1
            i += 1
            out.append(f'<pre dir="ltr" class="code {html.escape(lang)}"><code>{html.escape(chr(10).join(body))}</code></pre>')
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped)
        if m:
            level = len(m.group(1))
            content = inline(m.group(2))
            anchor = slug(content, used)
            if headings is not None and level == 2:
                headings.append((anchor, content))
            out.append(f'<h{level} id="{anchor}">{content}</h{level}>')
            i += 1; continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            out.append("<hr>"); i += 1; continue
        if stripped.startswith("|") and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            head = split_row(stripped)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i])); i += 1
            thead = "".join(f"<th>{inline(c)}</th>" for c in head)
            tbody = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f'<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>')
            continue
        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[i])); i += 1
            out.append(f"<blockquote>{md_to_html(chr(10).join(block))}</blockquote>")
            continue
        if LIST_ITEM.match(line):
            first = LIST_ITEM.match(line)
            top, ordered = len(first.group(1)), first.group(2)[0].isdigit()

            def other_list(candidate: str) -> bool:  # a top-level item of the other kind starts a new list
                m2 = LIST_ITEM.match(candidate)
                return bool(m2) and len(m2.group(1)) <= top + 1 and m2.group(2)[0].isdigit() != ordered

            block = []
            while i < len(lines):
                cur = lines[i]
                if not cur.strip():
                    # a blank line ends the list unless another item or an indented continuation follows
                    nxt = lines[i + 1] if i + 1 < len(lines) else ""
                    if nxt.strip() and (LIST_ITEM.match(nxt) or nxt.startswith("  ")) and not other_list(nxt):
                        i += 1; continue
                    break
                if other_list(cur):
                    break
                if LIST_ITEM.match(cur) or cur.startswith("  "):
                    block.append(cur); i += 1; continue
                if block and lines[i - 1].strip() and not re.match(r"^\s*(#{1,6}\s|```|\||>)", cur):
                    block.append("  " + cur.strip()); i += 1; continue  # lazy continuation of the item's text
                break
            out.append(render_list(block))
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*(#{1,6}\s|```|\||>|([-*+]|\d+[.)])\s)", lines[i]):
            para.append(lines[i].strip()); i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def document_html(markdown: str, meta: dict) -> str:
    headings: list = []
    body = md_to_html(markdown, headings)
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)) if title_match else meta["footer"]
    if title_match:
        body = body.replace(title_match.group(0), "", 1)
    toc = ""
    if len(headings) >= 4:
        toc = ('<nav class="toc" aria-label="المحتويات"><h2 class="toc-title">المحتويات</h2><ol>'
               + "".join(f'<li><a href="#{a}">{t}</a></li>' for a, t in headings) + "</ol></nav>")
    fonts = FONTS_DIR.as_uri()
    return f"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
@font-face{{font-family:"Messiri";font-weight:500 600;src:url({fonts}/el-messiri-arabic-600-normal.woff2) format("woff2");unicode-range:U+0600-06FF,U+0750-077F,U+0870-08FF,U+200C-200E,U+2010-2011,U+204F,U+2E41,U+FB50-FDFF,U+FE70-FEFC}}
@font-face{{font-family:"Messiri";font-weight:700 900;src:url({fonts}/el-messiri-arabic-700-normal.woff2) format("woff2");unicode-range:U+0600-06FF,U+0750-077F,U+0870-08FF,U+200C-200E,U+2010-2011,U+204F,U+2E41,U+FB50-FDFF,U+FE70-FEFC}}
@font-face{{font-family:"Messiri";font-weight:500 600;src:url({fonts}/el-messiri-latin-600-normal.woff2) format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+2000-206F,U+20AC,U+2122,U+2212,U+FEFF,U+FFFD}}
@font-face{{font-family:"Messiri";font-weight:700 900;src:url({fonts}/el-messiri-latin-700-normal.woff2) format("woff2");unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+2000-206F,U+20AC,U+2122,U+2212,U+FEFF,U+FFFD}}
:root{{--night:#15130f;--ink:#1b1915;--muted:#5d574c;--line:#e6dccb;--paper:#f8f4ec;--gold:#c9a464;--gold-2:#ead6a6;--gold-ink:#76561f;--olive-dark:#5a4116}}
*{{box-sizing:border-box}}
html{{direction:rtl}}
body{{margin:0;color:var(--ink);font-family:"Noto Naskh Arabic","Noto Sans Arabic","DejaVu Sans","Segoe UI",Tahoma,sans-serif;font-size:10.2pt;line-height:1.8;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.cover{{background:var(--night);color:#fdfaf4;border-radius:10pt;padding:16pt 20pt 14pt;margin:0 0 14pt;position:relative;overflow:hidden}}
.cover:after{{content:"";position:absolute;inset:0;background:radial-gradient(ellipse 60% 90% at 0% 0%,rgba(201,164,100,.28),transparent 60%);pointer-events:none}}
.cover .kicker{{font-family:"Messiri",serif;font-weight:600;color:var(--gold-2);font-size:10.5pt;letter-spacing:.02em}}
.cover h1{{font-family:"Messiri",serif;font-weight:700;font-size:24pt;line-height:1.35;margin:4pt 0 6pt;color:#fdfaf4}}
.cover .meta{{font-size:8.6pt;color:#e6dcc8}}
.cover .bar{{height:3pt;border-radius:2pt;background:linear-gradient(90deg,#f0dcae,#d3ad6b 42%,#b28642);margin-top:9pt;width:38%}}
h1,h2,h3,h4{{font-family:"Messiri","DejaVu Sans",serif;color:var(--night);line-height:1.45;break-after:avoid;page-break-after:avoid}}
h2{{font-size:15.5pt;font-weight:700;margin:20pt 0 6pt;padding-bottom:3pt;border-bottom:1.6pt solid var(--gold)}}
h3{{font-size:12.2pt;font-weight:700;margin:14pt 0 4pt;color:var(--olive-dark)}}
h4{{font-size:10.8pt;font-weight:600;margin:10pt 0 3pt;color:var(--olive-dark)}}
p{{margin:4pt 0 7pt}}
a{{color:var(--gold-ink);text-decoration:underline;text-underline-offset:2pt;overflow-wrap:break-word}}
strong{{color:#0f0d0a}}
ul,ol{{margin:3pt 0 8pt;padding-inline-start:17pt}}
li{{margin:1.5pt 0}}
li>ul,li>ol{{margin:2pt 0 2pt}}
code{{font-family:"DejaVu Sans Mono",Consolas,monospace;font-size:.84em;background:#f3ead7;border-radius:3pt;padding:.5pt 3pt;direction:ltr;unicode-bidi:isolate;overflow-wrap:break-word}}
pre{{direction:ltr;text-align:left;unicode-bidi:isolate;background:#f7f2e8;border:1px solid var(--line);border-inline-end:3pt solid var(--gold);border-radius:6pt;padding:7pt 9pt;margin:6pt 0 9pt;font-size:8.1pt;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere;break-inside:avoid}}
pre code{{background:none;padding:0;font-size:inherit;border-radius:0}}
blockquote{{margin:8pt 0;padding:6pt 11pt;background:#f8f0e0;border-inline-start:3pt solid var(--gold);border-radius:4pt;break-inside:avoid}}
blockquote p{{margin:2pt 0}}
hr{{border:0;border-top:1px solid var(--line);margin:12pt 0}}
table{{width:100%;border-collapse:collapse;margin:6pt 0 10pt;font-size:8.6pt;line-height:1.6;break-inside:auto}}
thead{{display:table-header-group}}
tr{{break-inside:avoid;page-break-inside:avoid}}
th{{background:var(--night);color:var(--gold-2);font-family:"Messiri","DejaVu Sans",serif;font-weight:600;text-align:right;padding:4pt 6pt;border:1px solid #3a342a}}
td{{text-align:right;vertical-align:top;padding:3.5pt 6pt;border:1px solid var(--line)}}
tbody tr:nth-child(even) td{{background:#faf6ee}}
.fill{{background:#f6e7c4;border-bottom:1px dashed #a8803f;border-radius:2pt;padding:0 2pt}}
.box{{font-family:"DejaVu Sans",sans-serif;color:var(--gold-ink);font-size:1.15em}}
li:has(>.box){{list-style:none}}
.st{{font-weight:700;font-family:"DejaVu Sans",sans-serif}}
.st.ok{{color:#2f6b45}}.st.warn{{color:#94600f}}.st.bad{{color:#a3332c}}
.toc{{border:1px solid var(--line);border-radius:8pt;padding:4pt 14pt 6pt;margin:0 0 12pt;background:#fbf8f2;break-inside:avoid}}
.toc-title{{font-size:11.5pt;border:0;margin:6pt 0 2pt;padding:0}}
.toc ol{{columns:2;column-gap:22pt;margin:2pt 0;font-size:9.2pt}}
.toc a{{color:var(--ink);text-decoration:none}}
</style>
</head>
<body>
<header class="cover">
  <div class="kicker">{html.escape(STORE)} — حزمة التسليم للعميل</div>
  <h1>{html.escape(title)}</h1>
  <div class="meta">إصدار النظام {html.escape(meta["version"])} · المراجعة (commit) <span dir="ltr">{html.escape(meta["commit"])}</span> · تاريخ إعداد الحزمة <span dir="ltr">{html.escape(meta["date"])}</span></div>
  <div class="bar"></div>
</header>
{toc}
{body}
</body>
</html>"""


# ---------------------------------------------------------------------------------------------------------------------
# throw-away server
# ---------------------------------------------------------------------------------------------------------------------
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(**extra: str) -> dict:
    env = dict(os.environ)
    # nothing from the developer's shell or .env may change how the throw-away store behaves
    for key in ("TRUST_PROXY", "PUBLIC_BASE_URL", "ALLOW_WEAK_PASSWORD", "AUTO_BACKUP", "BACKUP_KEEP", "LOG_FORMAT",
                "ADMIN_USERNAME", "ADMIN_PASSWORD", "HOST", "PORT", "DATA_DIR", "DIGITAL_ASSET_LINKS_FILE"):
        env.pop(key, None)
    env.update({"ENV_FILE": "off", "HOST": "127.0.0.1", "ADMIN_USERNAME": "admin"}, **extra)
    return env


class ThrowawayServer:
    def __init__(self, data_dir: Path, password: str):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = open(data_dir / "server.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-I", str(ROOT / "app.py")], cwd=data_dir,
                                     env=clean_env(PORT=str(self.port), DATA_DIR=str(data_dir), ADMIN_PASSWORD=password),
                                     stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(150):
            if self.proc.poll() is not None:
                raise RuntimeError("the throw-away server exited; see " + str(data_dir / "server.log"))
            try:
                with NO_PROXY.open(self.base + "/api/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("the throw-away server did not answer /api/health")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()  # SIGTERM to this PID only
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.log.close()


def api(base: str, method: str, path: str, body: dict | None = None, cookie: str = "", csrf: str = "") -> tuple[int, dict, dict]:
    request = urllib.request.Request(base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode("utf-8"))
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "make_handover.py")
    if cookie:
        request.add_header("Cookie", cookie)
    if csrf:
        request.add_header("X-CSRF-Token", csrf)
    try:
        with NO_PROXY.open(request, timeout=30) as r:
            return r.status, dict(r.headers.items()), json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from None


def seed(server: ThrowawayServer, password: str) -> None:
    """An About text, a description for the curtains category and one clearly-marked example contact message."""
    request = urllib.request.Request(server.base + "/api/admin/login", method="POST",
                                     data=json.dumps({"username": "admin", "password": password}).encode())
    request.add_header("Content-Type", "application/json")
    with NO_PROXY.open(request, timeout=30) as r:
        csrf = json.loads(r.read())["csrf"]
        jar = SimpleCookie()
        for header in r.headers.get_all("Set-Cookie") or []:
            jar.load(header)
    cookie = f"fakhama_admin={jar['fakhama_admin'].value}"
    _, _, current = api(server.base, "GET", "/api/admin/settings", cookie=cookie)
    settings = current["settings"]
    # the shop's own default About text (facts the owner gave), saved through the admin API like the owner would
    api(server.base, "PUT", "/api/admin/settings", {"about_title": settings.get("about_title") or "من نحن",
                                                    "about_body": settings["about_body"]}, cookie, csrf)
    _, _, cats = api(server.base, "GET", "/api/admin/categories", cookie=cookie)
    curtains = next(c for c in cats["categories"] if c["slug"] == "curtains")
    api(server.base, "PUT", "/api/admin/categories/curtains",
        {"name": curtains["name"], "active": True, "description": CATEGORY_DESCRIPTION}, cookie, csrf)
    api(server.base, "POST", "/api/contact", EXAMPLE_MESSAGE)
    api(server.base, "POST", "/api/admin/logout", {}, cookie, csrf)


# ---------------------------------------------------------------------------------------------------------------------
# package parts
# ---------------------------------------------------------------------------------------------------------------------
def node_env() -> dict:
    env = dict(os.environ)
    if not env.get("NODE_PATH"):
        try:
            env["NODE_PATH"] = run(["npm", "root", "-g"]).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            pass
    return env


def render(mode: str, payload, scratch: Path, env: dict | None = None) -> None:
    job = scratch / f"{mode}-jobs.json"
    job.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    result = subprocess.run(["node", str(RENDERER), mode, str(job)], env=env or node_env(), text=True, capture_output=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"tools/render_pdf.cjs {mode} failed (exit {result.returncode})")


def build_source_zip(out: Path) -> None:
    target = out / "01-Website" / "source-code.zip"
    target.parent.mkdir(parents=True)
    run(["git", "-C", str(ROOT), "archive", "--format=zip", "--prefix=institute-webset/", "-o", str(target), "HEAD"])
    with zipfile.ZipFile(target) as zf:
        bad = [n for n in zf.namelist() if FORBIDDEN.search(n)]
    if bad:
        target.unlink()
        raise SystemExit("source-code.zip would contain files that must never be delivered: " + ", ".join(bad))
    dirty = run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    if dirty:
        log("NOTE: source-code.zip is the last commit (HEAD); uncommitted changes to tracked files are NOT in it:\n  "
            + dirty.replace("\n", "\n  "))


def build_database(out: Path, scratch: Path) -> None:
    folder = out / "02-Database"
    folder.mkdir(parents=True)
    data = scratch / "db-fresh"
    data.mkdir()
    env = clean_env(DATA_DIR=str(data), PORT=str(free_port()), ADMIN_PASSWORD=secrets.token_urlsafe(18))
    # `backup` creates the database (schema + seed data) without starting the web server
    run([sys.executable, "-I", str(ROOT / "app.py"), "backup"], cwd=data, env=env)
    run([sys.executable, "-I", str(ROOT / "app.py"), "export-sql", str(folder / "database-sample.sql")], cwd=data, env=env)
    conn = sqlite3.connect(data / "store.db")
    try:
        tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        indexes = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY rowid").fetchall()
        version = re.search(r'^VERSION = "([^"]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M).group(1)
    finally:
        conn.close()
    lines = [f"-- {STORE} — database schema (SQLite), app version {version}",
             "-- Created automatically by app.py (init_db) on first start; older databases are upgraded in place.",
             "-- Field-by-field description: DATABASE.md in the source code and 03-Documentation/Technical-Documentation.pdf.",
             "PRAGMA foreign_keys = ON;", ""]
    lines += [sql.strip() + ";\n" for _name, sql in tables]
    lines += [sql.strip() + ";" for (sql,) in indexes]
    (folder / "schema.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = {}
    sample = (folder / "database-sample.sql").read_text(encoding="utf-8")
    for table in re.findall(r'^INSERT INTO "([^"]+)"', sample, re.M):
        counts[table] = counts.get(table, 0) + 1
    summary = "، ".join(f"{name}: {n}" for name, n in sorted(counts.items()) if name != "sqlite_sequence")
    (folder / "README.txt").write_text(f"""{STORE} — قاعدة البيانات
====================================================

schema.sql
  مخطط قاعدة البيانات (SQLite): كل الجداول والفهارس. ينشئه الخادم تلقائيًا عند أول تشغيل،
  ويُرقّي القواعد الأقدم تلقائيًا. شرح الحقول في 03-Documentation/Technical-Documentation.pdf.

database-sample.sql
  نموذج لقاعدة بيانات جديدة تمامًا (ليست بيانات المتجر الحقيقية)، أُنشئ بالأمر:
      python3 app.py export-sql database-sample.sql
  على مجلد بيانات مؤقت فارغ. يحوي الإعدادات الافتراضية والأقسام الخمسة و18 منتجًا من صور المتجر
  (السعر 0 = «السعر حسب الطلب») ولا يحوي طلبات أو رسائل أو إحصاءات.
  عدد الصفوف: {summary}.
  صفوف جدول admins لا تُصدَّر أبدًا (تجزئة كلمة المرور ومفتاح التحقق بخطوتين).
  للاستيراد في قاعدة جديدة:  sqlite3 store.db < database-sample.sql
  ثم شغّل الخادم مع ADMIN_PASSWORD (12 خانة فأكثر) لإنشاء حساب مدير جديد.

البيانات الحقيقية (المنتجات والطلبات والرسائل والصور)
  لا تُحفظ في هذه الحزمة. مصدرها الوحيد هو الخادم الحي:
  لوحة الإدارة ← إعدادات المتجر ← «النسخ الاحتياطي» ← «تنزيل نسخة احتياطية الآن (ZIP)».
  الملف fakhama-backup-<الوقت>.zip يحوي store.db كاملة وكل الصور المرفوعة وmanifest.json.
  احفظ نسخة خارج الخادم مرة كل أسبوع على الأقل (مثل Google Drive الخاص بصاحب المتجر)، ولا تشاركه:
  فيه بيانات العملاء وتجزئة كلمة مرور المدير.
  الاستعادة (بعد إيقاف الخادم):  python3 app.py restore fakhama-backup-<الوقت>.zip
""", encoding="utf-8")


def copy_design(out: Path) -> Path:
    """docs/design/design-system.html -> 04-Design/ with the files it references under ../../assets/ copied next to it."""
    folder = out / "04-Design"
    folder.mkdir(parents=True, exist_ok=True)
    page = DESIGN_PAGE.read_text(encoding="utf-8")
    refs = sorted(set(re.findall(r"\.\./\.\./assets/([A-Za-z0-9._/-]+)", page)))
    for ref in refs:
        source = (ROOT / "assets" / ref).resolve()
        if not source.is_relative_to((ROOT / "assets").resolve()) or not source.is_file():
            raise SystemExit(f"design-system.html references a missing file: assets/{ref}")
        target = folder / "assets" / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if any(r.startswith("fonts/") for r in refs):
        shutil.copy2(FONTS_DIR / "LICENSE-El-Messiri.txt", folder / "assets" / "fonts" / "LICENSE-El-Messiri.txt")
    target = folder / "design-system.html"
    target.write_text(page.replace("../../assets/", "assets/"), encoding="utf-8")
    return target


def write_texts(out: Path, meta: dict) -> None:
    (out / "04-Design" / "FIGMA.txt").write_text(f"""{STORE} — ملف التصميم في Figma
==========================================

الرابط: {FIGMA_URL}

ما يحويه ملف Figma:
  - متغيرات الألوان والمقاسات (Variables)، و14 نمطًا للنصوص (Text styles)، و5 أنماط للتأثيرات (Effect styles)،
    والتدرج الذهبي، وصور المتجر.

ما لم يكتمل فيه:
  - المكوّنات (Components) والشاشات (Screens): توقّف بناؤها بسبب حدود خطة Figma Starter
    (حصة استدعاءات MCP وحد الصفحات الثلاث للملف).
  - لذلك المرجع البصري الكامل هو هذا المجلد 04-Design:
      design-system.html / design-system.pdf  — الألوان والخطوط والأزرار والشارات والبطاقات والحركة بالقيم الحقيقية من الكود
      screens/                                — لقطات كاملة لكل صفحات المتجر وشاشات الإدارة بعرض 390 و1366 بكسل
  - ترقية فريق Figma إلى خطة Professional تسمح بإكمال المكوّنات والشاشات في الملف نفسه.

ملاحظات:
  - اللقطات أُخذت من خادم تجريبي مؤقت بمتصفح Chromium على Linux؛ نص الصفحات يستخدم خط الجهاز،
    فيظهر على الهواتف بخط النظام العربي، بينما العناوين بخط El Messiri المضمّن في الموقع.
  - الرسالة الظاهرة في صندوق الرسائل مثال تجريبي، والشريط «نسخة تجريبية» يظهر لأن خيار
    «بيانات المتجر حقيقية وجاهزة للنشر» غير مفعّل في الخادم التجريبي.
  - المراجعة (commit): {meta["commit"]} — تاريخ الإعداد: {meta["date"]}
""", encoding="utf-8")
    history = run(["git", "-C", str(ROOT), "log", "--date=short", "--format=%ad  %h  %s%n%b"]).stdout
    history = re.sub(r"\n{3,}", "\n\n", history).strip()
    (out / "CHANGELOG.txt").write_text(f"{STORE} — سجل التغييرات (git log)\nالمراجعة الحالية: {meta['commit']} — {meta['date']}\n"
                                       + "=" * 60 + "\n\n" + history + "\n", encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if re.search(r"^[ \t]*ADMIN_PASSWORD[ \t]*=[ \t]*[^\s#]", example, re.M):
        raise SystemExit(".env.example contains a password value; refusing to deliver it")
    (out / ".env.example").write_text(example, encoding="utf-8")


def check_no_secret(out: Path, secret: str) -> None:
    """The throw-away admin password must not appear in any delivered file (also inside the source zip)."""
    needle = secret.encode()
    for file in out.rglob("*"):
        if not file.is_file():
            continue
        if FORBIDDEN.search(file.relative_to(out).as_posix()):
            raise SystemExit(f"refusing to deliver {file.relative_to(out)}")
        data = file.read_bytes()
        if needle in data:
            raise SystemExit(f"a temporary password leaked into {file.relative_to(out)}")
        if file.suffix == ".zip":
            with zipfile.ZipFile(file) as zf:
                for name in zf.namelist():
                    if needle in zf.read(name):
                        raise SystemExit(f"a temporary password leaked into {file.name}:{name}")


def prepare_out(out: Path) -> None:
    if out.exists():
        entries = list(out.iterdir())
        if entries and not (out / "00-README.pdf").exists() and not (out / "01-Website").exists():
            raise SystemExit(f"{out} exists and does not look like a previous handover package; choose another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return str(size)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the client handover package.")
    parser.add_argument("--out", default=str(ROOT / "dist" / "CLIENT_HANDOVER"), help="output folder (default: dist/CLIENT_HANDOVER)")
    parser.add_argument("--no-screens", action="store_true", help="skip the screenshots (faster; for checking the documents)")
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    if out == ROOT or ROOT.is_relative_to(out):
        raise SystemExit("--out must not be the repository itself or one of its parents")

    commit = run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]).stdout.strip()
    version = re.search(r'^VERSION = "([^"]+)"', (ROOT / "app.py").read_text(encoding="utf-8"), re.M).group(1)
    meta = {"commit": commit, "version": version, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    prepare_out(out)
    password = "Hv" + secrets.token_urlsafe(24)

    with tempfile.TemporaryDirectory(prefix="handover-") as tmp:
        scratch = Path(tmp)
        log("1/6 source code (git archive HEAD)")
        build_source_zip(out)
        log("2/6 database schema and sample")
        build_database(out, scratch)

        log("3/6 design system")
        design = copy_design(out)
        if not args.no_screens:
            log("4/6 screenshots from a throw-away server")
            data = scratch / "server-data"
            data.mkdir()
            server = ThrowawayServer(data, password)
            try:
                seed(server, password)
                env = node_env()
                env["HANDOVER_ADMIN_PASSWORD"] = password
                render("screens", {"base": server.base, "out": str(out / "04-Design" / "screens"), "username": "admin",
                                   "widths": WIDTHS, "public": PUBLIC_SCREENS, "admin": ADMIN_SCREENS}, scratch, env)
            finally:
                server.stop()
        else:
            log("4/6 screenshots skipped (--no-screens)")

        log("5/6 documents (Markdown -> HTML -> PDF)")
        jobs = []
        for source, target, footer in DOCS:
            markdown = (HANDOVER_DOCS / source).read_text(encoding="utf-8")
            page = scratch / (Path(source).stem + ".html")
            page.write_text(document_html(markdown, {**meta, "footer": footer}), encoding="utf-8")
            pdf = out / target
            pdf.parent.mkdir(parents=True, exist_ok=True)
            jobs.append({"html": str(page), "pdf": str(pdf), "title": footer})
        jobs.append({"html": str(design), "pdf": str(out / "04-Design" / "design-system.pdf"), "title": "نظام التصميم", "settle": 600})
        render("pdf", jobs, scratch)

        log("6/6 FIGMA.txt, CHANGELOG.txt, .env.example")
        write_texts(out, meta)
        check_no_secret(out, password)

    total = 0
    log(f"\nHandover package: {out}")
    for file in sorted(p for p in out.rglob("*") if p.is_file()):
        size = file.stat().st_size
        total += size
        log(f"  {human(size):>9}  {file.relative_to(out).as_posix()}")
    log(f"  {human(total):>9}  total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
