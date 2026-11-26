"""Build the final report, report.pdf. Run from project root:
  .venv\\Scripts\\python scripts\\build_report.py

Gathers live empirical facts by running the real codebase, tests, and API
services at build time to produce a publication-grade academic and technical report.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crossdev.palette_fixer import app as fixer_app  # noqa: E402
from crossdev.palette_fixer import fixer, mock_vibecheck  # noqa: E402
from paletteai import clustering, color as C, etl, ranking  # noqa: E402
from paletteai import plugins as pa_plugins  # noqa: E402
from paletteai.samples import SAMPLES  # noqa: E402
SAMPLE_TITLES = {k: v[0] for k, v in SAMPLES.items()}
from paletteai.main import create_app  # noqa: E402

# ============================================================ Canvas with Page Numbers
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, num_pages):
        self.saveState()
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#64748b"))

        # Don't draw header on title page
        if self._pageNumber > 1:
            self.drawString(16 * mm, A4[1] - 11 * mm, "PaletteAI: Final Technical Report  |  CS698B Course Project")
            self.drawRightString(A4[0] - 16 * mm, A4[1] - 11 * mm, "Team LARPERS")
            self.setStrokeColor(colors.HexColor("#cbd5e1"))
            self.setLineWidth(0.5)
            self.line(16 * mm, A4[1] - 12.5 * mm, A4[0] - 16 * mm, A4[1] - 12.5 * mm)

        # Footer on all pages
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(16 * mm, 13 * mm, A4[0] - 16 * mm, 13 * mm)
        self.drawString(16 * mm, 9.5 * mm, "Indian Institute of Technology Kanpur (IITK)")
        self.drawRightString(A4[0] - 16 * mm, 9.5 * mm, f"Page {self._pageNumber} of {num_pages}")
        self.restoreState()


# ============================================================ Layout & Styling
W = A4[0] - 32 * mm
ss = getSampleStyleSheet()

PRIMARY = colors.HexColor("#0d366b")
SECONDARY = colors.HexColor("#0284c7")
TEXT_DARK = colors.HexColor("#1e293b")
TEXT_MUTED = colors.HexColor("#475569")
BG_LIGHT = colors.HexColor("#f8fafc")
BORDER_COLOR = colors.HexColor("#cbd5e1")

TITLE = ParagraphStyle("TitleStyle", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=26, leading=30, textColor=PRIMARY, alignment=TA_CENTER)
SUBTITLE = ParagraphStyle("SubTitleStyle", parent=ss["Normal"], fontName="Helvetica", fontSize=13, leading=17, textColor=SECONDARY, alignment=TA_CENTER)
H1 = ParagraphStyle("Heading1Style", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=13.5, leading=17, spaceBefore=14, spaceAfter=5, textColor=PRIMARY, keepWithNext=True)
H2 = ParagraphStyle("Heading2Style", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4, textColor=TEXT_DARK, keepWithNext=True)
H3 = ParagraphStyle("Heading3Style", parent=ss["Heading3"], fontName="Helvetica-Bold", fontSize=9, leading=12, spaceBefore=7, spaceAfter=3, textColor=SECONDARY, keepWithNext=True)
BODY = ParagraphStyle("BodyStyle", parent=ss["Normal"], fontName="Helvetica", fontSize=8.5, leading=12, spaceAfter=4, textColor=TEXT_DARK, alignment=TA_JUSTIFY)
BULLET = ParagraphStyle("BulletStyle", parent=BODY, leftIndent=12, spaceAfter=2.5, alignment=TA_LEFT)
CELL = ParagraphStyle("CellStyle", parent=ss["Normal"], fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=TEXT_DARK)
CELLB = ParagraphStyle("CellBStyle", parent=CELL, fontName="Helvetica-Bold", textColor=PRIMARY)
MONO = ParagraphStyle("MonoStyle", parent=ss["Code"], fontName="Courier", fontSize=5.5, leading=6.5, textColor=colors.HexColor("#0f172a"))
NOTE_TEXT = ParagraphStyle("NoteTextStyle", parent=BODY, fontName="Helvetica-Oblique", fontSize=8, leading=11, textColor=colors.HexColor("#78350f"))

story: list = []


def P(text, style=BODY):
    story.append(Paragraph(text, style))


def h1(t):
    P(t, H1)


def h2(t):
    P(t, H2)


def h3(t):
    P(t, H3)


def bullets(items):
    for it in items:
        P("&bull;&nbsp; " + it, BULLET)
    story.append(Spacer(1, 3))


def callout(title: str, text: str, kind: str = "note"):
    bg = colors.HexColor("#fffbeb") if kind == "note" else colors.HexColor("#f0f9ff")
    border = colors.HexColor("#d97706") if kind == "note" else colors.HexColor("#0284c7")
    content = [
        Paragraph(f"<b>{escape(title)}</b>", ParagraphStyle("CTitle", parent=CELLB, fontSize=8, textColor=border)),
        Spacer(1, 2),
        Paragraph(text, NOTE_TEXT if kind == "note" else CELL),
    ]
    t = Table([[content]], colWidths=[W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.8, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 4))


def table(rows, widths, header=True, extra=()):
    data = []
    for i, row in enumerate(rows):
        formatted_row = []
        for c in row:
            if isinstance(c, (Paragraph, Table, RLImage)):
                formatted_row.append(c)
            else:
                s = str(c)
                formatted_row.append(Paragraph(escape(s), CELLB if (header and i == 0) else CELL))
        data.append(formatted_row)
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")))
    # subtle alternating row backgrounds
    for r in range(1 if header else 0, len(rows)):
        if r % 2 == 1:
            style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc")))
    t.setStyle(TableStyle(style + list(extra)))
    story.append(t)
    story.append(Spacer(1, 5))


def strip(hexes, shares):
    total = 0.28 * W
    widths = [max(4, s * total) for s in shares]
    t = Table([[""] * len(hexes)], colWidths=widths, rowHeights=[8.5])
    t.setStyle(TableStyle([("BACKGROUND", (i, 0), (i, 0), colors.HexColor(h)) for i, h in enumerate(hexes)]
                          + [("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#475569"))]))
    return t


def png(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


# ============================================================ Diagram Generators
def generate_diagram(draw_fn, width=1200, height=400, target_width_ratio=1.0) -> RLImage:
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    draw_fn(d, width, height)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    target_w = target_width_ratio * W
    target_h = target_w * (height / width)
    return RLImage(buf, width=target_w, height=target_h)


def draw_box(d, x1, y1, x2, y2, title, subtitle="", fill=(248, 250, 252), outline=(13, 54, 107), text_color=(13, 54, 107), sub_color=(71, 85, 105)):
    font_bold = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 22)
    font_sub = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    d.rounded_rectangle([x1, y1, x2, y2], radius=12, fill=fill, outline=outline, width=3)
    d.text((x1 + 16, y1 + 14), title, fill=text_color, font=font_bold)
    if subtitle:
        d.text((x1 + 16, y1 + 44), subtitle, fill=sub_color, font=font_sub)


def draw_arrow(d, x1, y1, x2, y2, color=(2, 132, 199), width=4):
    d.line([x1, y1, x2, y2], fill=color, width=width)
    # arrow head
    dx = x2 - x1
    dy = y2 - y1
    angle = math.atan2(dy, dx)
    head_len = 14
    p1 = (x2 - head_len * math.cos(angle - 0.45), y2 - head_len * math.sin(angle - 0.45))
    p2 = (x2 - head_len * math.cos(angle + 0.45), y2 - head_len * math.sin(angle + 0.45))
    d.polygon([ (x2, y2), p1, p2 ], fill=color)


def arch_diagram_draw(d, w, h):
    d.rectangle([0, 0, w, h], fill=(255, 255, 255))
    # Layer 1: Client
    draw_box(d, 30, 30, 340, 370, "1. Client Layer", "Plain HTML / CSS / JavaScript\n5 tabs (Extract, Library,\nContrast, Harmony, Developers)\nNo CDN, no build step",
             fill=(240, 249, 255), outline=(2, 132, 199))
    draw_arrow(d, 340, 200, 390, 200)

    # Layer 2: API Gateway
    draw_box(d, 390, 30, 770, 370, "2. Application & API Layer", "FastAPI + Uvicorn\nPydantic request/response models\nOne error shape: {code, message}\nOpenAPI 3.1 at /docs\nPlugin system (local + webhook)",
             fill=(248, 250, 252), outline=(13, 54, 107))
    draw_arrow(d, 770, 200, 820, 200)

    # Layer 3: Engines & Persistence
    draw_box(d, 820, 30, 1170, 185, "3. Colour & ML code (NumPy)", "sRGB / CIELAB, WCAG, CVD\nK-Means++ with silhouette\nKernel preference ranking",
             fill=(254, 243, 199), outline=(217, 119, 6))
    draw_box(d, 820, 215, 1170, 370, "4. Persistent Storage", "SQLite (data/paletteai.db)\nSHA-256 hash skips repeat work\nNormalised image previews",
             fill=(241, 245, 249), outline=(71, 85, 105))


def etl_diagram_draw(d, w, h):
    d.rectangle([0, 0, w, h], fill=(255, 255, 255))
    steps = [
        ("Upload", "<= 10 MB, Pillow verify", 30),
        ("Hash", "SHA-256 Idempotency", 195),
        ("Normalise", "EXIF Orient, Frame 1", 360),
        ("Filter", "Alpha >= 128 Threshold", 525),
        ("Sample", "Nearest-Neighbour 5K", 690),
        ("Transform", "sRGB -> CIELAB (D65)", 855),
        ("Storage", "SQLite Atomicity", 1020),
    ]
    font_b = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 16)
    font_s = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13)
    for i, (title, sub, x) in enumerate(steps):
        d.rounded_rectangle([x, 50, x + 150, 220], radius=10, fill=(240, 249, 255), outline=(13, 54, 107), width=2)
        d.text((x + 12, 70), f"Step {i+1}", fill=(2, 132, 199), font=font_s)
        d.text((x + 12, 95), title, fill=(13, 54, 107), font=font_b)
        d.text((x + 12, 135), sub, fill=(71, 85, 105), font=font_s)
        if i < len(steps) - 1:
            draw_arrow(d, x + 150, 135, x + 195, 135)


def aiml_diagram_draw(d, w, h):
    d.rectangle([0, 0, w, h], fill=(255, 255, 255))
    steps = [
        ("1. Lab Sampling", "Up to 5000 opaque pixels", 40),
        ("2. K-Means++", "Lloyd's in Lab (3 restarts)", 270),
        ("3. Silhouette Eval", "k = 3..8 subsampled 1000px", 500),
        ("4. Fallback Guard", "If sil < 0.40 -> k = 5", 730),
        ("5. Delta-E Merge", "Merge if dE < 5.0", 960),
    ]
    font_b = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 17)
    font_s = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
    for i, (title, sub, x) in enumerate(steps):
        d.rounded_rectangle([x, 40, x + 200, 200], radius=10, fill=(254, 243, 199), outline=(217, 119, 6), width=2)
        d.text((x + 15, 65), title, fill=(180, 83, 9), font=font_b)
        d.text((x + 15, 115), sub, fill=(71, 85, 105), font=font_s)
        if i < len(steps) - 1:
            draw_arrow(d, x + 200, 120, x + 270, 120, color=(217, 119, 6))


def crossdev_diagram_draw(d, w, h):
    d.rectangle([0, 0, w, h], fill=(255, 255, 255))
    draw_box(d, 30, 40, 290, 240, "VibeCheck Host", "Team rm -rf sleep\nScores poster plans\nSends check.completed\nto our POST /hook", fill=(240, 249, 255), outline=(2, 132, 199))
    draw_arrow(d, 290, 140, 350, 140)

    draw_box(d, 350, 40, 840, 240, "Palette Fixer (our plugin)",
             "1. Read event, fetch team palette (GET /teams/{id}/style-profile)\n2. CIEDE2000 to nearest team colour; > 10 = off-brand\n3. Up to 3 swaps that keep text at WCAG AA (4.5:1)\n4. Recolour images, re-score via POST /teams/{id}/checks\n5. Log the run in SQLite (fixer_log.db)",
             fill=(248, 250, 252), outline=(13, 54, 107))
    draw_arrow(d, 840, 140, 900, 140)

    draw_box(d, 900, 40, 1160, 240, "Panel in VibeCheck", "Shown in its result page\nScore now vs with swaps\nEach swap with Delta-E\nContrast after swaps\nBar chart",
             fill=(254, 243, 199), outline=(217, 119, 6))


# ============================================================ Gather Live Facts
print("Gathers live facts from codebase and running server...")
tmp = tempfile.mkdtemp(prefix="paletteai_report_")
client = TestClient(create_app(data_dir=Path(tmp) / "data", plugins_dir=ROOT / "plugins"))
api = "/api/v1"
spec = client.get("/openapi.json").json()
(ROOT / "docs" / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")

samples = {}
for s in client.get(f"{api}/samples").json():
    samples[s["id"]] = client.post(f"{api}/samples/{s['id']}/extract").json()["palette"]
recached = client.post(f"{api}/samples/sunset/extract").json()["cached"]
# Real photographs: the same image with automatic k and with k fixed at 6.
photo_k6 = {name: client.post(f"{api}/samples/{name}/extract", params={"k": 6}).json()["palette"]
            for name in samples if name.startswith("iitk_")}

stripes = np.zeros((40, 160, 3))
truth = [(226, 86, 60), (28, 52, 84), (245, 239, 224), (44, 142, 132)]
for i, c in enumerate(truth):
    stripes[:, i * 40:(i + 1) * 40] = c
flat = client.post(f"{api}/images", files={"file": ("stripes.png", png(stripes), "image/png")}).json()["palette"]
noise = client.post(f"{api}/images", files={"file": ("noise.png", png(np.random.default_rng(3).integers(0, 256, (120, 120, 3))), "image/png")}).json()["palette"]
bad_empty = client.post(f"{api}/images", files={"file": ("empty.png", b"", "image/png")})
bad_text = client.post(f"{api}/images", files={"file": ("notes.png", b"not an image", "image/png")})

for p in samples.values():
    client.put(f"{api}/palettes/{p['id']}/save", json={"user": "demo"})
cold = client.get(f"{api}/ranking", params={"user": "demo"}).json()
client.put(f"{api}/palettes/{samples['sunset']['id']}/feedback", json={"user": "demo", "value": "like"})
for name in ("mono", "forest"):
    client.put(f"{api}/palettes/{samples[name]['id']}/feedback", json={"user": "demo", "value": "dislike"})
warm = client.get(f"{api}/ranking", params={"user": "demo"}).json()


class _Bridge(httpx.BaseTransport):
    def __init__(self, target):
        self.c = TestClient(target)

    def handle_request(self, request):
        r = self.c.request(request.method, request.url.path, params=dict(request.url.params),
                           content=request.read(), headers=dict(request.headers))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": r.headers.get("content-type", "")})


fixer_app.LOG_PATH = Path(tmp) / "fixer.db"
fixer_app.app.state.transport = _Bridge(mock_vibecheck.app)
ev = json.loads(json.dumps(mock_vibecheck.SAMPLE_EVENT))
ev["check"]["score"] = mock_vibecheck.score_image("1", mock_vibecheck.demo_poster())
fixer_client = TestClient(fixer_app.app)
panel = fixer_client.post("/hook", json=ev).json()
run = fixer_client.get("/runs").json()[0]["payload"]

# Run pytests to gather live verification statistics
py = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
collected = subprocess.run(py + ["--collect-only"], cwd=ROOT, capture_output=True, text=True).stdout
result_line = [ln for ln in subprocess.run(py, cwd=ROOT, capture_output=True, text=True).stdout.splitlines() if " passed" in ln or " failed" in ln][-1]
tests: dict[str, list[str]] = {}
for ln in collected.splitlines():
    m = re.match(r"tests[\\/](test_\w+)\.py::(\w+)", ln)
    if m:
        tests.setdefault(m.group(1), [])
        if m.group(2) not in tests[m.group(1)]:
            tests[m.group(1)].append(m.group(2))
n_cases = len(re.findall(r"^tests[\\/]", collected, flags=re.M))


# Real runs against the partner's VibeCheck, read from the plugin's own run log (if present).
real_runs = []
_log_db = ROOT / "crossdev" / "palette_fixer" / "data" / "fixer_log.db"
if _log_db.exists():
    import sqlite3
    _seen = set()
    with sqlite3.connect(_log_db) as _c:
        for _at, _check, _payload in _c.execute(
                # From 26 Nov 2026: VibeCheck with real CLIP. Earlier runs were its fake-CLIP test mode.
                "SELECT at, check_id, payload FROM runs WHERE status = 'ok' AND at >= '2026-11-26' ORDER BY id DESC"):
            _p = json.loads(_payload)
            _ev = _p.get("event") or {}
            if not str(_ev.get("api_base", "")).endswith(":8030") or not _p.get("rescore"):
                continue
            _key = (_ev["team"]["name"], tuple(s["from"] for s in _p["fix"]["swaps"]))
            if _key in _seen:
                continue
            _seen.add(_key)
            real_runs.append({"at": _at, "check": _check, "team": _ev["team"]["name"], "p": _p})
            if len(real_runs) >= 4:
                break
    real_runs.reverse()


# ============================================================ TITLE & COVER PAGE
P("PaletteAI", TITLE)
P("Colour palettes from images, with accessibility checks and a library that learns what you like", SUBTITLE)
story.append(Spacer(1, 8))
P("CS698B Final Project Report &nbsp;|&nbsp; IIT Kanpur &nbsp;|&nbsp; November 2026",
  ParagraphStyle("c", parent=BODY, alignment=TA_CENTER, fontName="Helvetica-Bold", textColor=TEXT_MUTED))
story.append(Spacer(1, 12))

table([
    ["Part", "Project", "Code", "Main tools"],
    ["Self-dev", "PaletteAI", "paletteai/ (FastAPI + plain HTML/CSS/JS)", "NumPy, CIELAB, K-Means, silhouette score, WCAG 2.1, SQLite"],
    ["Cross-dev", "Palette Fixer & Re-Scorer, a plugin for VibeCheck", "crossdev/palette_fixer/", "CIEDE2000, VibeCheck's plugin webhook and API, SQLite"],
], [0.12, 0.30, 0.28, 0.30])

story.append(Spacer(1, 6))
h2("Team LARPERS")
table([
    ["#", "Name", "Roll No.", "Email", "Main work (details in Section 1)"],
    ["1", "Jagrab Jyoti Deuri", "265017", "jagrabjd26@iitk.ac.in", "K-Means clustering with silhouette selection; colour science module"],
    ["2", "Neilan Ghosh", "265023", "neilang26@iitk.ac.in", "Preference ranking model; cross-dev colour fixing engine (CIEDE2000)"],
    ["3", "Kamlesh Malviya", "265019", "kamleshm26@iitk.ac.in", "Interface styling, sample images, demo guide, report assembly"],
    ["4", "Tarun Kumar", "265030", "tarunkumar26@iitk.ac.in", "REST API and plugin system; connecting the plugin to VibeCheck"],
    ["5", "Sagnik Saha", "265026", "sagniks26@iitk.ac.in", "ETL pipeline and SQLite storage; core unit tests"],
], [0.04, 0.19, 0.10, 0.25, 0.42])

h2("Cross-dev partner")
table([
    ["Base project", "Team", "Members", "What we used"],
    ["VibeCheck (checks whether a poster plan fits a design team's style)", "rm -rf sleep",
     "Pranil (265025), Niteesh (265024), Sreejit (265028), Neel (265022), Ekansh (265013)",
     "check.completed webhook, result-slot Panel, GET /teams/{id}/style-profile, POST /teams/{id}/checks, POST /plugins"],
], [0.28, 0.12, 0.30, 0.30])

story.append(Spacer(1, 6))
callout("Summary",
        "PaletteAI takes an image and gives back its main colours, how much of the image each one covers, whether they are "
        "readable as text on each other (WCAG contrast), and how they look to people with the three common kinds of colour "
        "blindness. It also suggests colour harmonies. Users can save palettes and like or dislike them, and the library is "
        "then re-ordered by a small model that learns from those likes. Everything runs locally on a laptop with no internet. "
        "For the cross-dev part we built Palette Fixer & Re-Scorer for team rm -rf sleep's VibeCheck: when VibeCheck says a "
        "plan is off-brand, the plugin suggests up to three colour swaps towards the team's own colours, checks that text "
        "stays readable, and asks VibeCheck to score the recoloured version. Numbers in Part C were produced by running the "
        "code while this PDF was built.",
        kind="info")

story.append(PageBreak())

# ============================================================ 1. WHO DID WHAT
h1("1. Who Did What")
P("We split the work by component so that each person owned a part of the code from start to end, and then everyone "
  "tested the whole thing together before the demo. The table lists what each of us built and the files that came "
  "out of it. Reviewing each other's code and fixing bugs found during testing was shared by everyone.")
table([
    ["Member", "What they did", "Main files"],
    ["Kamlesh Malviya",
     "Styled the web interface (layout, tabs, light/dark theme switch, loader, sample grid) and checked that the colours "
     "used in the UI itself pass contrast. Put together the sample image set, including finding the three IIT Kanpur photos "
     "on Wikimedia Commons and adding their credits. Wrote the demo guide and assembled this report.",
     "paletteai/static/*.css, index.html, samples.py, docs/DEMO.md, scripts/build_report.py"],
    ["Neilan Ghosh",
     "Designed and built the preference ranking model: the eight palette features, the kernel score, the weights that "
     "adapt to what a user likes, and the plain-English reasons shown next to each ranked palette. For the cross-dev plugin, "
     "wrote the colour fixing engine: CIEDE2000 (checked against the published test pairs), off-brand detection, the swap "
     "budget, the rule that a swap may not make text unreadable, and the recolouring of images used for re-scoring.",
     "paletteai/ranking.py, crossdev/palette_fixer/fixer.py"],
    ["Jagrab Jyoti Deuri",
     "Built the clustering engine: K-Means with k-means++ seeding in CIELAB, choosing the number of colours by silhouette "
     "score, the fallback when the score cannot decide, and merging near-identical colours. Wrote the colour science module: "
     "sRGB to CIELAB, WCAG contrast, the colour-blindness simulation matrices and the harmony rules.",
     "paletteai/clustering.py, paletteai/color.py"],
    ["Sagnik Saha",
     "Built the ETL pipeline: upload checks (size, type, broken files, decompression bombs), EXIF rotation, transparency "
     "handling, pixel sampling, the content hash that skips repeat work, and the SQLite schema. Wrote most of the core unit tests.",
     "paletteai/etl.py, service.py, db.py, tests/test_core.py"],
    ["Tarun Kumar",
     "Built the REST API and its typed request/response models, the error format, and the plugin system (in-process plugins, "
     "webhook plugins, panel blocks, timeouts), with the example plugins and plugin docs. For the cross-dev part, wrote the "
     "code that talks to VibeCheck (reading its event, fetching the team palette, submitting re-score checks, building its "
     "Panel), registered the plugin, and tested it against VibeCheck. Wrote the API and plugin tests.",
     "paletteai/api.py, schemas.py, plugins.py, main.py, crossdev/palette_fixer/client.py, app.py, tests/test_api.py, tests/test_palette_fixer.py"],
], [0.16, 0.56, 0.28])

# ============================================================ PART A: SELF-DEV PROJECT
story.append(Spacer(1, 8))
h1("Part A: Self-Dev Project (PaletteAI)")

h1("2. What PaletteAI Is and Why We Built It")
P("Many palette tools have three problems. They measure colour distance in RGB, where equal "
  "numeric steps do not look equal to the eye, so the palettes come out muddy. They make you pick the number of colours "
  "yourself. And they say nothing about whether the colours can actually be used together, for example as text on a "
  "background, or how they look to someone who is colour blind (about 8% of men and 0.5% of women have some form of it).")
P("PaletteAI tries to fix those three things and nothing more. It works in CIELAB, a colour space built so that distances "
  "roughly match what people see. It picks the number of colours itself and tells you why. It checks every pair of colours "
  "against the WCAG contrast rules and shows the palette under protanopia, deuteranopia and tritanopia. On top of that, the "
  "library learns from likes and dislikes and puts the palettes you are likely to want first. We kept to the scope in our "
  "proposal: the app recommends and explains; it does not edit images.")

h2("2.1 The five components at a glance")
table([
    ["Component", "What it does in PaletteAI", "Where"],
    ["ETL", "Accepts an upload, checks it (size, type, broken or fully transparent files), fixes rotation, drops transparent "
            "pixels, samples up to 5,000 pixels, converts them to CIELAB and stores the image, palette, contrast results and "
            "feedback in SQLite. A SHA-256 hash of the file means the same image is never processed twice.",
     "etl.py, service.py, db.py"],
    ["AIML", "K-Means in CIELAB for the dominant colours, with the number of colours (3 to 8) chosen by silhouette score and a "
             "fallback to 5 when the score is inconclusive. A preference model ranks saved palettes from each user's likes and dislikes.",
     "clustering.py, ranking.py"],
    ["VIZ", "Swatches labelled with HEX, a plain colour name and coverage; WCAG pass/fail written in words; colour-blindness "
            "previews of the image and palette; harmony suggestions; charts for composition, silhouette scores and ranking, "
            "each backed by a real data table.",
     "static/, color.py"],
    ["APP", "A local web app: FastAPI backend, plain HTML/CSS/JS frontend (no build step), SQLite. Runs with one command and "
            "needs no internet. Ships with 12 sample images so the demo always works.",
     "main.py, run.py, samples.py"],
    ["DEV", "A documented REST API (OpenAPI at /docs and /openapi.json) covering every feature of the UI, and a plugin system "
            "so other teams can add routes and result panels without changing our code.",
     "api.py, schemas.py, plugins.py"],
], [0.10, 0.62, 0.28])

h2("2.2 How the pieces fit together")
P("The browser only ever talks to the REST API, the same API another team would use. That was a deliberate choice: "
  "if something works in the UI, it works for a script or a plugin too.")
story.append(generate_diagram(arch_diagram_draw, width=1200, height=400, target_width_ratio=1.0))
story.append(Spacer(1, 6))

# ------------------------------------------------------------ 3. Colour maths
h1("3. The Colour Maths We Used")
P("All of this is written directly in NumPy in color.py; we did not use a computer vision library. The formulas are the "
  "standard ones, and we list them here because the rest of the report depends on them.")

h2("3.1 sRGB to CIELAB (D65 white point)")
bullets([
    "<b>Undo the gamma:</b> each channel c in [0, 1] becomes c / 12.92 if c &le; 0.04045, otherwise ((c + 0.055) / 1.055)<sup>2.4</sup>.",
    "<b>Linear RGB to XYZ:</b> multiply by the standard sRGB matrix, rows [0.4124564, 0.3575761, 0.1804375], "
    "[0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041].",
    "<b>XYZ to Lab:</b> divide by the white point (0.95047, 1.0, 1.08883), apply f(t) = t<sup>1/3</sup> (or a linear piece for "
    "very small t), then L* = 116 f(Y) - 16, a* = 500 (f(X) - f(Y)), b* = 200 (f(Y) - f(Z)).",
])

h2("3.2 Colour difference")
P("Inside PaletteAI we use plain Euclidean distance in Lab (Delta-E 76), which is good enough for clustering and for "
  "deciding when two clusters are the same colour. For the cross-dev plugin we needed something more careful, because "
  "the plugin tells a designer \"this colour is off-brand\", so it uses <b>CIEDE2000</b> (Sharma, Wu and Dalal, 2005). "
  "CIEDE2000 adds weights for lightness, chroma and hue and a correction term for blues, where Lab is least uniform.")

h2("3.3 WCAG contrast")
P("Relative luminance is Y = 0.2126 R + 0.7152 G + 0.0722 B on linear RGB. For a lighter colour L1 and darker L2 the "
  "contrast ratio is (L1 + 0.05) / (L2 + 0.05), from 1:1 (same colour) to 21:1 (black on white). We report AA (4.5:1 "
  "normal text, 3:1 large text) and AAA (7:1).")

h2("3.4 Colour-blindness simulation")
P("We use the matrices from Machado, Oliveira and Fernandes (2009) at full severity, applied in linear RGB:")
table([
    ["Type", "What is missing", "Matrix rows"],
    ["Protanopia", "Red (L) cones", "[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]"],
    ["Deuteranopia", "Green (M) cones", "[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]"],
    ["Tritanopia", "Blue (S) cones", "[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]"],
], [0.15, 0.17, 0.68])

# ------------------------------------------------------------ 4. ETL
h1("4. ETL: Getting Images In and Storing the Results")
P("Data comes from users: images they upload, plus the 12 built-in samples. Every upload is treated as untrusted. "
  "The pipeline either produces a clean set of Lab pixels or rejects the file with a clear error, and nothing is "
  "written to the database unless the whole extraction succeeds.")
story.append(generate_diagram(etl_diagram_draw, width=1200, height=260, target_width_ratio=1.0))

h2("4.1 Checks on the way in")
bullets([
    "Empty files are rejected (400). Files over 10 MB are rejected (413). Images over 40 megapixels are rejected before "
    "decoding, which stops decompression bombs.",
    "Only PNG, JPEG, WebP, GIF and BMP are accepted (415 otherwise), and Pillow has to be able to verify the file (422 if not).",
    "For animated GIFs we use the first frame. Phone photos are rotated using their EXIF orientation so the preview is the right way up.",
    "Pixels with alpha below 128 are ignored, so a logo on a transparent background does not get a fake white or black colour. "
    "A completely transparent image is rejected with the code <code>fully_transparent</code>.",
])
P(f"During the build of this report, an empty upload came back with HTTP {bad_empty.status_code} and a text file renamed "
  f"to .png came back with HTTP {bad_text.status_code}; neither created any rows.")

h2("4.2 Sampling")
P("The image is shrunk to fit in 200 x 200 using nearest-neighbour resizing. We picked nearest-neighbour on purpose: "
  "smoother filters blend pixels at sharp edges and invent colours that were never in the image. Up to 5,000 opaque "
  "pixels are then sampled. The random generator is seeded from the file's SHA-256 hash, so the same image always gives "
  "the same palette, which made testing much easier.")

h2("4.3 Storage")
P("Everything is kept in one SQLite file (data/paletteai.db): images (hash, name, size, transparency), palettes (colours "
  "with HEX, Lab and coverage, the k used, the silhouette scores and whether the fallback was used), the contrast and "
  "colour-blindness results, each user's saved palettes and their likes and dislikes. The image and its palette are "
  "written in one transaction. Uploading the same file with the same k again returns the stored palette instead of "
  f"re-clustering (in this build, re-submitting the sunset sample returned cached = {str(recached).lower()}).")

story.append(PageBreak())

# ------------------------------------------------------------ 5. AIML
h1("5. AIML: Finding the Colours and Learning Preferences")
P("There are two adaptive parts. The clustering decides how many colours an image really has, and the ranking model "
  "changes its ordering as a user likes and dislikes palettes.")
story.append(generate_diagram(aiml_diagram_draw, width=1200, height=240, target_width_ratio=1.0))

h2("5.1 K-Means in CIELAB")
P("We wrote K-Means ourselves in NumPy instead of pulling in scikit-learn. Starting points are chosen with k-means++ "
  "(each new centre is picked with probability proportional to its squared distance from the nearest existing centre). "
  "We run 3 restarts of up to 40 iterations and keep the best. If a cluster ends up empty, it is restarted at the point "
  "that is currently worst served, so we always get exactly k colours.")

h2("5.2 Choosing the number of colours")
P(f"In automatic mode we try k = {min(clustering.AUTO_K_RANGE)} to {max(clustering.AUTO_K_RANGE)} and compute the mean "
  "silhouette score for each on a fixed subsample of 1,000 pixels. For a pixel i, s(i) = (b(i) - a(i)) / max(a(i), b(i)), "
  "where a(i) is its mean distance to its own cluster and b(i) its mean distance to the nearest other cluster. Higher is "
  "better. The k with the best mean score wins.")
callout("When the silhouette score can't decide",
        f"If the best score is below {clustering.MIN_CONFIDENT_SILHOUETTE}, or all the scores are within "
        f"{clustering.MIN_SILHOUETTE_SPREAD} of each other, no k is clearly better. Pure noise is the typical case (it scores "
        f"around 0.3 for every k). We then use k = {clustering.FALLBACK_K} and the result page says, in words, that the "
        "fallback was used and why.",
        kind="note")

h2("5.3 Merging near-identical colours")
P(f"Gradients often split into two clusters that look the same. In automatic mode, any two centres closer than "
  f"{clustering.MERGE_DELTA_E} Delta-E are merged into their weighted average, repeatedly, until no such pair is left.")

h2("5.4 Preference ranking")
P("Each palette is turned into eight numbers between 0 and 1:")
table([
    ["Feature", "Meaning", "How it is computed"],
    ["mean_lightness", "How light the palette is overall", "Coverage-weighted mean L*, divided by 100"],
    ["lightness_spread", "Light-to-dark range", "Weighted standard deviation of L*, divided by 40"],
    ["mean_chroma", "How saturated overall", "Weighted mean chroma (distance from grey in a*, b*), divided by 80"],
    ["max_chroma", "How vivid the strongest accent is", "Largest chroma, divided by 120"],
    ["contrast_spread", "Best contrast available inside the palette", "log(max contrast ratio) / log(21)"],
    ["size", "How many colours", "k divided by 10"],
    ["warmth", "Warm vs cool", "Chroma-weighted projection onto the yellow-orange direction (70 degrees) in Lab"],
    ["hue_diversity", "How many different hues", "1 minus the length of the mean hue vector; faded out for near-grey palettes"],
], [0.18, 0.32, 0.50])
P(f"A palette x is scored as the average similarity to the palettes the user liked minus the average similarity to the "
  f"ones they disliked. Similarity is a Gaussian kernel, K(u, v) = exp(-&Sigma; w<sub>j</sub>(u<sub>j</sub> - v<sub>j</sub>)<sup>2</sup> "
  f"/ (2&sigma;<sup>2</sup>)), with &sigma; = {ranking.KERNEL_WIDTH}. The weights w<sub>j</sub> are what makes it adapt: "
  "w<sub>j</sub> = 1 + 4 |mean of liked<sub>j</sub> - mean of disliked<sub>j</sub>|, scaled so they average 1. If a user's "
  "likes and dislikes differ mostly in, say, warmth, warmth starts to count more than the other features. With no feedback "
  "every palette scores 0 and the library keeps its normal order. Each ranked palette also gets a short reason, such as "
  "\"warmer than the palettes you disliked\".")

# ------------------------------------------------------------ 6. VIZ
h1("6. VIZ: What the User Sees")
P("Our rule was that colour is never the only way something is shown. Every result that uses colour also says it in text.")
bullets([
    "<b>Swatches</b> show the HEX code, a plain name (for example \"dark blue\", \"cream\", or \"red (darker)\" / \"red (lighter)\" "
    "when two reds appear) and the percentage of the image they cover. The label switches between black and white, whichever "
    "is more readable on that swatch.",
    "<b>Contrast</b>: a full table of every colour pair, with the ratio and AA / AAA pass or fail written out. There is also a "
    "separate contrast checker for any two colours.",
    "<b>Colour-blindness previews</b> of both the image and the palette for protanopia, deuteranopia and tritanopia.",
    "<b>Harmonies</b>: complementary, analogous, triadic, split-complementary, tetradic and monochromatic, from any base colour.",
    "<b>Charts</b> for palette composition, the silhouette score of every k that was tried, the learned feature weights and the "
    "ranking scores. Each chart is a real HTML table underneath, so a screen reader can read it.",
    "<b>Keyboard and screen readers</b>: tabs work with the arrow, Home and End keys, focus is always visible, there is a "
    "skip link, and status messages are announced.",
    "<b>Themes and motion</b>: light and dark themes, Windows high-contrast mode is respected, and animations stop when the "
    "system asks for reduced motion.",
    "<b>Small things we fixed after testing</b>: the result now scrolls into view and takes focus after extraction, and one "
    "borrowed input style had white text on orange at about 2.8:1, so we darkened the orange to about 4.9:1.",
])

story.append(PageBreak())

# ------------------------------------------------------------ 7. APP & DEV
h1("7. APP and DEV: Running It and Building On It")
h2("7.1 The app")
P("PaletteAI is one Python process: FastAPI with Uvicorn, serving the API and the static frontend. The frontend is plain "
  "HTML, CSS and JavaScript with no framework and no build step, and nothing is loaded from the internet, so it works "
  "offline on the demo laptop. Setup is <code>pip install -r requirements.txt</code> and then <code>python run.py</code>, "
  "which opens http://127.0.0.1:8000. There is no login; instead, a profile name keeps each person's library and "
  "preferences separate, which is enough for a local app. The tabs are Extract, Library &amp; ranking, Contrast, Harmony "
  "and Developers.")
P("The 12 sample images are nine drawn in code (including a logo with transparency and a grey gradient, both edge cases) "
  "and three photos of IIT Kanpur from Wikimedia Commons, credited on their cards.")

h2("7.2 The plugin system")
P("Other teams can add features without touching our code, in two ways:")
table([
    ["Kind", "How it is added", "What it can do", "Example"],
    ["In-process (Python)", "A folder plugins/<name>/plugin.py, loaded at start-up", "Add API routes under /api/v1/ext/<name>/ and panels in the result and library slots", "plugins/tints_and_shades"],
    ["Webhook (HTTP)", "POST /api/v1/plugins with a URL, or from the Developers tab", "Receive an event when a palette is shown and return a panel", "examples/webhook_plugin.py"],
], [0.18, 0.30, 0.34, 0.18])
P("Panels are not HTML. A plugin returns a list of typed blocks (<code>text</code>, <code>status</code>, <code>stat</code>, "
  "<code>swatches</code>, <code>table</code>, <code>bars</code>) and our frontend draws them, so a plugin cannot inject "
  f"scripts into the page. A webhook gets {pa_plugins.HOOK_TIMEOUT_SECONDS:g} seconds to answer. If it is slow, crashes or "
  "returns something invalid, its panel shows \"unavailable\" with the reason and the rest of the page works as normal. "
  "docs/PLUGINS.md has the full guide with request and response examples.")

# ------------------------------------------------------------ 8. API
h1("8. API Reference")
P(f"The full specification is OpenAPI {spec['openapi']}, generated by FastAPI from our typed models and served live at "
  "<code>/openapi.json</code> (interactive docs at <code>/docs</code>). A copy is saved to docs/openapi.json on every build. "
  "All routes are under <code>/api/v1</code> and use JSON unless stated.")
h2("8.1 Conventions")
bullets([
    "<b>Errors</b> always have the same shape: <code>{\"detail\": {\"code\": \"...\", \"message\": \"...\"}}</code>. The code is "
    "machine-readable (for example <code>file_too_large</code>, <code>unsupported_type</code>, <code>fully_transparent</code>, "
    "<code>not_found</code>, <code>validation_error</code>) and the message is meant for people.",
    "<b>Users</b> are identified by a <code>user</code> field or query parameter (the profile name). There is no authentication.",
    "<b>Colours</b> are given and returned as HEX strings (#RRGGBB); the short form #RGB is accepted on input.",
    "<b>Status codes</b>: 200 OK, 201 created, 204 deleted, 400 bad input, 404 not found, 409 already exists, 413 too large, "
    "415 wrong file type, 422 the request or image could not be processed.",
])

h2("8.2 Endpoints")


def _schema_name(s):
    if not s:
        return ""
    if "$ref" in s:
        return s["$ref"].split("/")[-1]
    if s.get("type") == "array":
        return "array of " + (_schema_name(s.get("items")) or "items")
    if "anyOf" in s:
        return " or ".join(_schema_name(x) or x.get("type", "") for x in s["anyOf"])
    return s.get("type", "")


def _fields(name):
    sch = spec["components"]["schemas"].get(name, {})
    req = set(sch.get("required", []))
    out = []
    for k, v in (sch.get("properties") or {}).items():
        out.append(f"{k}{'*' if k in req else ''}: {_schema_name(v) or 'value'}")
    return "; ".join(out)


for path, ops in spec["paths"].items():
    for method, op in ops.items():
        rows = [["", f"{method.upper()} {path}"]]
        rows.append(["Purpose", op.get("summary", "")])
        desc = (op.get("description") or "").strip().split("\n\n")[0].replace("\n", " ")
        if desc:
            rows.append(["Details", desc[:400]])
        params = op.get("parameters", [])
        if params:
            rows.append(["Parameters", "; ".join(
                f"{p['name']} ({p['in']}{', required' if p.get('required') else ''}, {_schema_name(p.get('schema')) or 'value'})"
                for p in params)])
        body = (op.get("requestBody") or {}).get("content") or {}
        for ctype, b in body.items():
            nm = _schema_name(b.get("schema"))
            f = _fields(nm) if nm else ""
            if nm.startswith("Body_"):
                nm = "form fields"
            rows.append(["Request body", f"{ctype}: {nm}" + (f" ({f})" if f else "")])
        resp = []
        for code, r in op.get("responses", {}).items():
            ok_schema = ""
            for _ct, c in (r.get("content") or {}).items():
                ok_schema = _schema_name(c.get("schema"))
            if code.startswith("2"):
                resp.append(f"{code} {r.get('description', '')}" + (f" -> {ok_schema}" if ok_schema else ""))
            else:
                resp.append(f"{code} {r.get('description', '')}")
        rows.append(["Responses", "; ".join(resp)])
        cells = [[Paragraph(f"<font face='Courier-Bold'>{escape(rows[0][1])}</font>", CELLB), ""]]
        cells += [[Paragraph(f"<b>{escape(a)}</b>", CELL), Paragraph(escape(b), CELL)] for a, b in rows[1:]]
        t = Table(cells, colWidths=[0.16 * W, 0.84 * W])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER_COLOR),
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        story.append(KeepTogether([t, Spacer(1, 5)]))

h2("8.3 Data models")
P("Fields marked * are required. These are the response and request models referred to above.")
schema_rows = [["Model", "Fields"]]
for name, sch in sorted(spec["components"]["schemas"].items()):
    if sch.get("properties") and not name.startswith(("HTTPValidation", "Validation", "Body_")):
        schema_rows.append([name, _fields(name)])
table(schema_rows, [0.22, 0.78])

h2("8.4 Example calls")
story.append(Preformatted(
    "# upload an image and get its palette (automatic number of colours)\n"
    "curl -F file=@poster.png http://127.0.0.1:8000/api/v1/images\n\n"
    "# same, but force 6 colours\n"
    "curl -F file=@poster.png -F k=6 http://127.0.0.1:8000/api/v1/images\n\n"
    "# save palette 3 for user 'asha' and like it, then get her ranking\n"
    "curl -X PUT -H \"Content-Type: application/json\" -d '{\"user\":\"asha\"}' http://127.0.0.1:8000/api/v1/palettes/3/save\n"
    "curl -X PUT -H \"Content-Type: application/json\" -d '{\"user\":\"asha\",\"value\":\"like\"}' http://127.0.0.1:8000/api/v1/palettes/3/feedback\n"
    "curl \"http://127.0.0.1:8000/api/v1/ranking?user=asha\"\n\n"
    "# check a text/background pair\n"
    "curl -H \"Content-Type: application/json\" -d '{\"foreground\":\"#FFFFFF\",\"background\":\"#0D366B\"}' http://127.0.0.1:8000/api/v1/contrast\n\n"
    "# register a webhook plugin\n"
    "curl -H \"Content-Type: application/json\" -d '{\"name\":\"my-plugin\",\"hook_url\":\"http://127.0.0.1:9000/hook\",\"slots\":[\"palette.result\"]}' http://127.0.0.1:8000/api/v1/plugins",
    ParagraphStyle("Ex", parent=MONO, fontSize=6.8, leading=8.4)))
story.append(Spacer(1, 4))
P("Error example (a 15 MB upload): <code>413 {\"detail\": {\"code\": \"file_too_large\", \"message\": \"...\"}}</code>.")

# ============================================================ PART B: CROSS-DEV PROJECT
story.append(PageBreak())
h1("Part B: Cross-Dev Project (Palette Fixer &amp; Re-Scorer for VibeCheck)")

h1("9. What the Plugin Does")
P("VibeCheck, by team rm -rf sleep, learns a design team's visual style from its past posts and tells a designer, before "
  "they design, how well their plan fits that style: a score out of 100, the reasons, and the closest past posts. One of "
  "the reasons it often gives is that the colours are far from the team's signature palette. It says what is wrong but "
  "not how to fix it. Our plugin fills that gap.")
P("After every VibeCheck check, the plugin looks at the plan's colours, finds the ones that are furthest from the team's "
  "signature colours, and suggests up to three swaps. It makes sure a swap does not make the main text unreadable. Then "
  "it recolours the checked images with those swaps and submits them to VibeCheck as a new check, so the \"score with "
  "these swaps\" number is VibeCheck's own score, not a guess of ours. The result appears as a panel inside VibeCheck's "
  "result page. The plugin only suggests; it never changes the designer's files.")
story.append(generate_diagram(crossdev_diagram_draw, width=1200, height=280, target_width_ratio=1.0))

h2("9.1 How it connects to VibeCheck")
table([
    ["VibeCheck feature (from their API)", "How we use it"],
    ["POST /plugins {name, url, slot: \"result\"}", "Registers our webhook, http://127.0.0.1:8010/hook, once."],
    ["check.completed event, POSTed to our URL", "Gives us the check (score, reasons, plan_palette with coverage, description, image URLs), the team and api_base."],
    ["GET /teams/{id}/style-profile", "The team's signature_palette, which is what we move colours towards."],
    ["POST /teams/{id}/checks (multipart images, description, kind)", "Re-scores the recoloured images. We tag the description with [palette-fixer re-score] and skip events for those checks, so the plugin never triggers itself."],
    ["Panel {title, summary, metrics, items, chart}", "What we return. VibeCheck draws it in its result page. It must arrive within 5 seconds."],
], [0.40, 0.60])

h1("10. The Five Components of the Plugin")
P("The course brief says a cross-dev plugin should use the base system's API rather than build its own pipelines, but still "
  "needs its own data, adaptive and visual parts. This is how each component looks in Palette Fixer.")
h2("10.1 ETL")
P("Input is VibeCheck's <code>check.completed</code> event. We parse it defensively (VibeCheck's field names are read in "
  "client.py only, so a change on their side means changing one file), validate and normalise every HEX value and drop "
  "anything that is not a colour. We then fetch the team's signature palette and, for re-scoring, download the checked images "
  "from VibeCheck. Every run is stored in a local SQLite file (fixer_log.db): the event, the signature palette, the swaps, the "
  "re-score result, how long it took and the outcome (ok, no colours, own re-score, error). <code>GET /runs</code> shows the "
  "latest runs, which is how we debugged it and how the numbers in Section 12.6 were collected.")
h2("10.2 AIML")
bullets([
    f"<b>Off-brand detection:</b> CIEDE2000 from each plan colour to its nearest signature colour. Further than "
    f"{fixer.MISMATCH_DELTA_E} is off-brand, as is any colour VibeCheck itself flagged in its reasons.",
    f"<b>Choosing swaps:</b> worst colours first, at most {fixer.MAX_SWAPS}. Each signature colour is used at most once, and a "
    "swap never creates a colour that is already in the palette, so the result stays a set of distinct colours.",
    "<b>Keeping text readable:</b> we guess which colour is the background (the one with the largest share) and which is the "
    "text (the one with the best contrast against it). A swap is only accepted if that pair still passes WCAG AA (4.5:1), or, "
    "if it already failed, does not get worse. If the direct swap breaks that, we try the signature colour's hue and chroma "
    "at the original colour's lightness. If that also fails, the colour is left alone and the panel says why.",
    "<b>Re-scoring:</b> each pixel of the checked images is moved by the same Lab offset as its nearest plan colour, which "
    "keeps texture and shading, and the result is sent to VibeCheck. Because VibeCheck's model learns from each team's own "
    "posts, the score we show adapts to that team, and it changes as VibeCheck retrains.",
])
h2("10.3 VIZ")
P("The panel uses VibeCheck's own Panel format so it looks native: the score now and with the swaps, how many colours were "
  "swapped, the text contrast after the swaps, a line per swap (\"Replace #3CF01A with #2FC3B5 (signature colour)\" with the "
  "Delta-E before and after), colours that were left alone and why, VibeCheck's own colour reason, and a bar chart of the "
  "average distance from the team palette before and after. Everything is written in words as well as colours.")
h2("10.4 APP")
P("A small FastAPI service (crossdev/palette_fixer/app.py) with <code>POST /hook</code> on port 8010, plus "
  "<code>/health</code>, <code>/runs</code> and its own <code>/docs</code>. It is started with "
  "<code>python -m crossdev.palette_fixer.app</code>. It can also be imported and called directly "
  "(<code>handle_event</code>) if VibeCheck ever wants to run it in-process. If VibeCheck's API is slow or down, or the "
  "check has no images, the plugin returns a short panel explaining that instead of failing.")
h2("10.5 DEV")
P("We built the plugin only from VibeCheck's published API and source on GitHub (Pro-Nil/vibecheck). We did not get any "
  "special access. For development we wrote a mock VibeCheck (mock_vibecheck.py) with the same request and response shapes, "
  "so we could test without their heavy CLIP model. The tests check our Panel against a copy of VibeCheck's Panel schema. "
  "The API section is left out here as the brief allows; VibeCheck's API is documented in their report and at their /docs.")

h1("11. Integration Problems We Hit")
P("The first version timed out every time against the real VibeCheck. VibeCheck called plugins from inside an async request "
  "handler, which blocked its server until the plugin answered. Our plugin calls VibeCheck back (for the style profile and "
  "the re-score), so it was waiting on a server that was waiting on it, until the 5-second limit ran out. We found this by "
  "reading their code, confirmed it with a one-line local change (running plugins in a thread pool with "
  "<code>run_in_threadpool</code>), and sent it to team rm -rf sleep. They added the fix to their repository in commit "
  "cf4beee, \"Run plugin calls and scoring off the event loop so plugins can call the API back\". The results below are from "
  "their GitHub version with no local changes.")
P("Two smaller things came up while testing. Checks with only a description and no images have no colours, so the plugin "
  "has nothing to fix; we changed its message to tell the user to add images. And every re-score shows up as an extra entry in "
  "VibeCheck's History, tagged [palette-fixer re-score]. That is honest about what happened, but it adds clutter, and it is "
  "something to agree with the VibeCheck team.")

# ============================================================ PART C: RESULTS AND TESTING
story.append(PageBreak())
h1("Part C: Results and Testing")
P("Everything in this part was produced by running the actual code while this PDF was being built (except Section 12.6, "
  "which is read from the plugin's log of runs against the real VibeCheck). If the code changes, the numbers change with it.")

h1("12. Results")
h2("12.1 Can it find colours we already know?")
P("We made a test image of four flat stripes with known colours, (226, 86, 60), (28, 52, 84), (245, 239, 224) and "
  "(44, 142, 132), and ran it through the normal upload endpoint.")
exact_match = {c["hex"] for c in flat["colors"]} == {"#%02X%02X%02X" % t for t in truth}
P(f"It chose k = {flat['k']} by itself and the colours came back "
  f"<b>{'exactly right' if exact_match else 'NOT exactly right'}</b>: "
  + ", ".join(f"{c['hex']} ({c['share']*100:.1f}%)" for c in flat["colors"]) + ".")

h2("12.2 What happens with pure noise?")
P(f"A 120 x 120 image of random pixels has no real clusters. Its best silhouette score was "
  f"<b>{max(s['silhouette'] for s in noise['k_scores']):.3f}</b>, under the {clustering.MIN_CONFIDENT_SILHOUETTE} threshold, "
  f"so the fallback was used (fallback_used = {str(noise['fallback_used']).lower()}, k = {noise['k']}). The reason shown to the "
  f"user was: <i>\"{escape(noise['reason'])}\"</i>")

h2("12.3 All sample images")
bench_rows = [["Sample", "k", "Silhouette", "Fallback", "Palette (by coverage)", "Top colours"]]
for name, p in list(samples.items()) + [("Stripes (known colours)", flat), ("Random noise", noise)]:
    bench_rows.append([
        SAMPLE_TITLES.get(name, name), str(p["k"]), "-" if p["silhouette"] is None else f"{p['silhouette']:.3f}",
        "Yes" if p["fallback_used"] else "No",
        strip([c["hex"] for c in p["colors"]], [c["share"] for c in p["colors"]]),
        ", ".join(f"{c['hex']} ({c['percent']:.0f}%)" for c in p["colors"][:3]),
    ])
table(bench_rows, [0.20, 0.05, 0.09, 0.08, 0.28, 0.30])

h2("12.4 Real photos: automatic k vs k = 6")
P("The drawn samples have clean colours. The three IIT Kanpur photos are harder: leaves, shadows, sky and brick. Each was "
  "extracted twice, once in automatic mode and once with k fixed at 6.")
photo_rows = [["Photo", "Auto k", "Silhouette", "Automatic", "k = 6"]]
for name, p6 in photo_k6.items():
    pa = samples[name]
    photo_rows.append([
        SAMPLE_TITLES[name], str(pa["k"]), "-" if pa["silhouette"] is None else f"{pa['silhouette']:.3f}",
        strip([c["hex"] for c in pa["colors"]], [c["share"] for c in pa["colors"]]),
        strip([c["hex"] for c in p6["colors"]], [c["share"] for c in p6["colors"]]),
    ])
table(photo_rows, [0.22, 0.08, 0.10, 0.30, 0.30])
P("On busy photos the silhouette score likes a small k: a few broad, well-separated groups score better than many "
  "overlapping ones, so small accents (flowers, signs) get absorbed into their neighbours. Fixing k brings those accents "
  "back, at the cost of clusters that overlap more. That is why the app lets you choose: automatic for a faithful "
  "summary, a fixed number when you want detail.")

h2("12.5 Does the ranking actually learn?")
P(f"Profile \"demo\" saved all {len(samples)} sample palettes. Before any feedback, every palette scored 0. We then liked "
  "<b>sunset</b> and disliked <b>mono</b> and <b>forest</b>:")
cold_ranks = {it["palette"]["image"]["filename"]: it["rank"] for it in cold["items"]}
rank_rows = [["New rank", "Palette", "Score", "Old rank", "Feedback", "Reason shown"]]
for it in warm["items"]:
    fn = it["palette"]["image"]["filename"]
    rank_rows.append([str(it["rank"]), fn, f"{it['score']:+.2f}", str(cold_ranks[fn]),
                      it["palette"]["feedback"] or "-", " ".join(it["reasons"])])
table(rank_rows, [0.09, 0.15, 0.09, 0.09, 0.10, 0.48])
learned_wts = sorted(warm["model"]["feature_weights"].items(), key=lambda kv: -kv[1])
warm_rank = {it["palette"]["image"]["filename"]: it["rank"] for it in warm["items"]}
P(f"The model went from \"{cold['model']['status']}\" to \"{warm['model']['status']}\". The features it now weighs most: "
  + ", ".join(f"{warm['model']['feature_labels'][k]} ({v:.2f})" for k, v in learned_wts[:4])
  + f". The liked palette moved to rank {warm_rank['sunset.png']} and the two disliked ones to ranks "
  f"{warm_rank['forest.png']} and {warm_rank['mono.png']} out of {len(warm['items'])}; palettes that resemble them moved with them.")

h2("12.6 The plugin against the real VibeCheck")
if real_runs:
    P("These are real runs from 26 November 2026, against VibeCheck from its GitHub repository (commit cf4beee) with its "
      "real CLIP model, two teams (our own test team \"jj\" and a synthetic \"Demo Studio\" team generated by VibeCheck's own data "
      "script) and sample ideas with off-brand colours. Scores are VibeCheck's own, out of 100.")
    rr = [["Team", "Swaps suggested", "Distance from team palette (avg)", "Text contrast", "VibeCheck score", "Time"]]
    for r in real_runs:
        p = r["p"]
        f = p["fix"]
        rr.append([
            r["team"],
            "; ".join(f"{s['from']} &rarr; {s['to']}" for s in f["swaps"]),
            f"{f['mean_delta_e_before']:.1f} &rarr; {f['mean_delta_e_after']:.1f}",
            f"{f['contrast']['before']}:1 &rarr; {f['contrast']['after']}:1 ({'passes AA' if f['contrast']['passes_aa'] else 'below AA'})",
            f"{p['event']['check']['score']} &rarr; {p['rescore']['score']}",
            f"{p.get('elapsed_ms', 0) / 1000:.1f} s",
        ])
    table([[Paragraph(c, CELLB if i == 0 else CELL) for c in row] for i, row in enumerate(rr)],
          [0.17, 0.27, 0.14, 0.20, 0.12, 0.10])
    P("The swaps always moved the colours much closer to the team's palette (average CIEDE2000 roughly halved) and kept the "
      "text readable where it was readable before. In one case (the pastel idea) the plan's text contrast was already below AA "
      "and the swaps left it unchanged, which is the rule working as intended. The score gains are real but small. That is "
      "expected and worth saying plainly: VibeCheck scores the whole look of a poster with CLIP (layout, shapes, type), and "
      "colour is only part of that, so fixing colours alone cannot turn an off-brand idea into an on-brand one. The slowest "
      f"run took {max(r['p'].get('elapsed_ms', 0) for r in real_runs) / 1000:.1f} s, inside VibeCheck's 5-second limit.")
else:
    callout("No real VibeCheck runs found",
            "The plugin's run log has no runs against VibeCheck on port 8030. Run a few checks with images in VibeCheck "
            "while the plugin is registered, then rebuild.", kind="note")

h2("12.7 The plugin against the mock (repeatable on every build)")
fix = run["fix"]
P(f"Because the real VibeCheck needs its own data and model, every build also runs the plugin against our mock with the "
  f"same API shapes, on team \"{ev['team']['name']}\" (signature colours {', '.join(run['signature'])}). The mock's score is "
  "just colour closeness, not VibeCheck's model.")
table([["Plan colour", "Suggested", "Delta-E before", "Delta-E after", "Kind of swap"]]
      + [[s["from"], s["to"], f"{s['delta_e_before']:.1f}", f"{s['delta_e_after']:.1f}", s["kind"]] for s in fix["swaps"]],
      [0.18, 0.18, 0.18, 0.18, 0.28])
table([
    ["", "Original", "With suggested swaps"],
    ["Palette", strip(fix["original"], [1 / len(fix["original"])] * len(fix["original"])),
     strip(fix["proposed"], [1 / len(fix["proposed"])] * len(fix["proposed"]))],
    ["Average CIEDE2000 to team palette", f"{fix['mean_delta_e_before']:.2f}", f"{fix['mean_delta_e_after']:.2f}"],
    ["Text / background contrast", f"{fix['contrast']['before']}:1",
     f"{fix['contrast']['after']}:1 ({'passes AA' if fix['contrast']['passes_aa'] else 'fails AA'})"],
    ["Score (mock)", str(ev["check"]["score"]), f"{run['rescore']['score']} ({run['rescore']['score'] - ev['check']['score']:+.1f})"],
], [0.30, 0.35, 0.35])

h1("13. Unit and Integration Tests")
P(f"We use pytest. During this build the suite ran with this result: <b>{escape(result_line.strip('= '))}</b>, "
  f"{n_cases} test cases in total. Run it yourself with <code>python -m pytest tests</code>.")
test_desc = {
    "test_core": "Unit tests for the pure functions: HEX parsing, WCAG contrast at known values (black/white = 21:1), "
                 "Lab round trips, the colour-blindness matrices, harmony angles, ETL limits and transparency, K-Means "
                 "recovering known clusters, the noise fallback, same input giving the same output, and ranking features "
                 "staying in [0, 1] with weights that move towards the liked palettes.",
    "test_api": "Integration tests through the real FastAPI app: exact colour recovery via upload, the hash cache, every "
                "error code, previews, separate libraries per user, feedback reversing the ranking, the colour tool "
                "endpoints, local and webhook plugins (including a slow plugin timing out without breaking the page), and "
                "the OpenAPI document.",
    "test_palette_fixer": "Cross-dev tests: CIEDE2000 against the published Sharma et al. (2005) pairs, HEX cleaning, "
                          "swap choice, the contrast rule, the three-swap limit, a full check.completed round trip against "
                          "the mock (including re-scoring and skipping our own re-score checks), in-process mode, and the Panel "
                          "matching VibeCheck's schema.",
}
table([["File", "Tests", "What it covers", ]]
      + [[f"tests/{f}.py", str(len(v)), test_desc.get(f, "") + " Examples: " + ", ".join(v[:4]) + "."] for f, v in tests.items()],
      [0.20, 0.07, 0.73])

# ============================================================ PART D
story.append(PageBreak())
h1("Part D: Scope, Third-Party Code and AI Use")

h1("14. Proposal vs What We Delivered")
P("We were marked against the features in our proposal, so we went through it line by line.")
table([
    ["Proposal said", "Where it is", "How to check"],
    ["ETL: check file type, size and integrity", "etl.py (10 MB limit, Pillow check)", "Section 4.1; empty and fake files rejected"],
    ["ETL: resize, handle transparency", "etl.py", "Logo sample: transparent pixels ignored and reported"],
    ["ETL: sample pixels, CIELAB, HEX + coverage", "etl.py, color.py", "Every palette in Section 12.3"],
    ["ETL: store images, palettes, accessibility results, feedback in SQLite", "db.py, service.py", "Section 4.3; feedback survives a restart"],
    ["ETL: content hash avoids repeat extraction", "service.py (SHA-256)", f"Sunset re-submitted: cached = {str(recached).lower()}"],
    ["ETL: bad files give clear errors, never enter the data", "etl.py, main.py", "Section 4.1, test_api.py"],
    ["AIML: K-Means in CIELAB", "clustering.py", "Section 12.1"],
    ["AIML: bounded k chosen by silhouette, with fallback", "clustering.py (k = 3-8, fallback 5)", "Sections 12.2 and 12.4"],
    ["AIML: ranking from likes/dislikes using lightness, chroma, contrast spread, size", "ranking.py (8 features)", "Section 12.5"],
    ["AIML: report clustering and ranking on test cases", "Part C", "Sections 12.1-12.5"],
    ["VIZ: labelled swatches, HEX, coverage", "static/app.js", "Any result page"],
    ["VIZ: WCAG ratios with pass/fail in text", "Contrast tab and palette table", "Pass/fail written out"],
    ["VIZ: protanopia, deuteranopia, tritanopia previews", "color.py", "Every result page"],
    ["VIZ: harmonies and a ranked library", "Harmony tab, Library tab", "Six schemes; reasons per palette"],
    ["VIZ: charts for composition and ranking", "Result and Library tabs", "Each chart also a table"],
    ["VIZ: keyboard, focus, labels, light/dark, never colour alone", "index.html, app.css, app.js", "Section 6"],
    ["APP: local FastAPI + HTML/CSS/JS + SQLite", "main.py, static/, db.py", "One command, no internet"],
    ["APP: upload, save, contrast, CVD, harmony, rate", "All tabs", "Demo"],
    ["APP: validation, error states, sample images", "etl.py, samples.py", f"{len(samples)} offline samples"],
    ["DEV: endpoints for every feature", "api.py", "Section 8"],
    ["DEV: OpenAPI at /docs and /openapi.json", "FastAPI", "Section 8, docs/openapi.json"],
    ["DEV: plugin interface and integration example", "plugins.py, plugins/, examples/", "Section 7.2, docs/PLUGINS.md"],
    ["Cross-dev: suggest swaps, keep contrast, re-score in VibeCheck, show it in its result slot", "crossdev/palette_fixer/", "Sections 9-12.6"],
], [0.38, 0.30, 0.32])

h1("15. Third-Party Code, Libraries and Services")
P("We only claim credit for the code we wrote. Everything below was written by other people and used under its licence. "
  "PaletteAI calls no outside service while it runs. The plugin's only outside service is VibeCheck itself, which is "
  "team rm -rf sleep's work, not ours.")
table([
    ["What", "Licence / source", "What we used it for"],
    ["FastAPI, Starlette, Uvicorn", "MIT / BSD", "Web framework, routing and server for both PaletteAI and the plugin"],
    ["Pydantic v2", "MIT", "Request and response models, validation, and the OpenAPI schema"],
    ["Pillow", "HPND", "Opening and checking images, EXIF rotation, previews"],
    ["NumPy", "BSD-3", "All the array maths: colour conversion, K-Means, silhouette, ranking"],
    ["HTTPX", "BSD-3", "Webhook calls, and the plugin's calls to VibeCheck"],
    ["python-multipart", "Apache-2.0", "Parsing file uploads"],
    ["SQLite (Python's sqlite3)", "Public domain", "Storage for PaletteAI and the plugin's run log"],
    ["pytest", "MIT", "Running the tests"],
    ["ReportLab", "BSD", "Building this PDF"],
    ["VibeCheck (team rm -rf sleep)", "Their project, github.com/Pro-Nil/vibecheck", "The base app for our plugin; we use its API and its scores"],
    ["Machado, Oliveira & Fernandes (2009)", "Paper", "Colour-blindness simulation matrices"],
    ["Sharma, Wu & Dalal (2005)", "Paper", "CIEDE2000 formula and its test pairs"],
    ["WCAG 2.1", "W3C Recommendation", "Luminance and contrast formulas and thresholds"],
    ["Theme switch, 3D button", "Uiverse.io (zl306, njesenberger), MIT", "CSS, adapted for contrast and keyboard focus"],
    ["Cube logo animation", "Uiverse.io (Admin12121), MIT", "Re-scaled into the header logo"],
    ["Loader", "Uiverse.io (Donewenfu), MIT", "Re-coloured; static when reduced motion is on"],
    ["Pixel-style input field", "Uiverse.io (xbeat_5120), MIT", "Orange darkened to pass WCAG AA"],
    ["Photo: IITK Library", "Wikimedia Commons, Lalujbad, CC BY-SA 3.0", "P.K.kelkar_library_iit_kanpur.jpg, cropped and resized; credited on its card"],
    ["Photo: IITK Computer Centre", "Wikimedia Commons, Dr Vinamra, CC BY-SA 4.0", "Computer_Center_at_IIT_Kanpur_India.jpg, cropped and resized; credited on its card"],
    ["Photo: IITK Hall 2", "Wikimedia Commons, Vaibhav.jndl, CC BY-SA 4.0", "Hall_2_IIT_Kanpur.jpg, cropped and resized; credited on its card"],
], [0.28, 0.30, 0.42])

h1("16. AI Usage (written by the team)")
# The course brief requires this section to be written by humans, not generated. The build
# copies the team's own text from docs/AI_USAGE.txt word for word and adds nothing.
# Format: blank-line-separated paragraphs; a line starting "## " is a sub-heading; lines starting "- " are bullets.
ai_usage_file = ROOT / "docs" / "AI_USAGE.txt"
if ai_usage_file.exists() and ai_usage_file.read_text(encoding="utf-8").strip():
    for block in re.split(r"\n\s*\n", ai_usage_file.read_text(encoding="utf-8").strip()):
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if lines[0].startswith("## "):
            h2(escape(lines[0][3:]))
            lines = lines[1:]
        if lines and all(ln.lstrip().startswith("- ") for ln in lines):
            bullets([escape(ln.lstrip()[2:]) for ln in lines])
        elif lines:
            P(escape(" ".join(ln.strip() for ln in lines)))
else:
    callout("TO BE WRITTEN BY THE TEAM BEFORE SUBMISSION",
            "Write this section yourselves in docs/AI_USAGE.txt and rebuild. The brief asks for a detailed, human-written "
            "account of: which AI tools and models were used; how access was obtained (free plan, paid plan, student "
            "access...); what each was used for (planning, proposal, coding, testing, report); and the team's experience "
            "with them: hallucinations, intent mismatches, token or rate limits, fixes you had to make. "
            "One short paragraph is not enough.", kind="note")

h1("17. Limitations and What We Would Do Next")
bullets([
    "<b>No real accounts.</b> Profiles are just names. That is fine for a local app, but a shared or hosted version would need proper logins.",
    "<b>Colour blindness is simulated at full strength only.</b> Many people have a milder form (anomalous trichromacy), which we do not model.",
    "<b>Automatic k is conservative on busy photos</b> (Section 12.4). A smarter rule, or showing two suggestions, would help.",
    "<b>No export</b> to design tools (Figma, Adobe swatches, CSS variables) yet. The JSON API has everything needed to add it.",
    "<b>Plugin score gains are small</b> because VibeCheck judges the whole look, not just colour (Section 12.6). Suggesting layout "
    "or type changes is outside what we proposed.",
    "<b>Re-score checks appear in VibeCheck's History.</b> A way to mark them hidden would need a small change on VibeCheck's side.",
])

story.append(PageBreak())

# ============================================================ APPENDIX
h1("Appendix A: Raw OpenAPI Document (start)")
P(f"The first part of the OpenAPI {spec['openapi']} JSON for {spec['info']['title']} v{spec['info']['version']}. The full "
  "file is docs/openapi.json and /openapi.json on the running app.")
lines = []
for ln in json.dumps(spec, indent=1, ensure_ascii=True).splitlines()[:500]:
    indent = len(ln) - len(ln.lstrip())
    lines += textwrap.wrap(ln, 160, subsequent_indent=" " * (indent + 2)) or [""]
for i in range(0, min(len(lines), 330), 110):
    story.append(Preformatted("\n".join(lines[i:i + 110]), MONO))

# ============================================================ Build PDF
out = Path(os.environ.get("REPORT_OUT", ROOT / "report.pdf"))   # REPORT_OUT: write elsewhere (e.g. while report.pdf is open)
print(f"Compiling document story into {out}...")
doc = SimpleDocTemplate(
    str(out),
    pagesize=A4,
    leftMargin=16 * mm,
    rightMargin=16 * mm,
    topMargin=15 * mm,
    bottomMargin=16 * mm,
    title="PaletteAI - Final Report",
    author="Team LARPERS",
)

doc.build(story, canvasmaker=NumberedCanvas)
print("SUCCESS: wrote", out, "| Live test verdict:", result_line.strip())
