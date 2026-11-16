"""Build report.pdf. Run from the project root:  .venv\\Scripts\\python scripts\\build_report.py

Numbers in the evaluation, API and testing sections are produced by running the
real code at build time, so the report cannot drift from the implementation.
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crossdev.palette_fixer import app as fixer_app  # noqa: E402
from crossdev.palette_fixer import fixer, mock_vibecheck  # noqa: E402
from paletteai import clustering, etl, ranking  # noqa: E402
from paletteai.main import create_app  # noqa: E402

W = A4[0] - 36 * mm
ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontSize=9.5, leading=13, spaceAfter=5)
CELL = ParagraphStyle("cell", parent=ss["Normal"], fontSize=8, leading=10)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#0d366b"))
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=8, spaceAfter=4)
NOTE = ParagraphStyle("note", parent=BODY, backColor=colors.HexColor("#fff4d6"), borderColor=colors.HexColor("#c98500"),
                      borderWidth=0.8, borderPadding=6, spaceBefore=6, spaceAfter=10)
MONO = ParagraphStyle("mono", parent=ss["Code"], fontSize=5.6, leading=6.6)
story: list = []


def P(text, style=BODY):
    story.append(Paragraph(text, style))


def h1(t):
    P(t, H1)


def h2(t):
    P(t, H2)


def bullets(items):
    for it in items:
        P("&bull;&nbsp; " + it, ParagraphStyle("b", parent=BODY, leftIndent=10, spaceAfter=2))
    story.append(Spacer(1, 4))


def table(rows, widths, header=True, extra=()):
    data = [[c if not isinstance(c, str) else Paragraph(escape(c) if i or not header else escape(c), CELLB if header and i == 0 else CELL)
             for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c3c2b7")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3eefb")))
    t.setStyle(TableStyle(style + list(extra)))
    story.append(t)
    story.append(Spacer(1, 8))


def strip(hexes, shares):
    """A palette drawn as coloured cells proportional to coverage."""
    total = 0.26 * W
    t = Table([[""] * len(hexes)], colWidths=[max(3, s * total) for s in shares], rowHeights=[9])
    t.setStyle(TableStyle([("BACKGROUND", (i, 0), (i, 0), colors.HexColor(h)) for i, h in enumerate(hexes)]
                          + [("BOX", (0, 0), (-1, -1), 0.3, colors.grey)]))
    return t


def png(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


# ============================================================ gather live facts
tmp = tempfile.mkdtemp(prefix="paletteai_report_")
client = TestClient(create_app(data_dir=Path(tmp) / "data", plugins_dir=ROOT / "plugins"))
api = "/api/v1"
spec = client.get("/openapi.json").json()
(ROOT / "docs" / "openapi.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")

samples = {}
for s in client.get(f"{api}/samples").json():
    samples[s["id"]] = client.post(f"{api}/samples/{s['id']}/extract").json()["palette"]
recached = client.post(f"{api}/samples/sunset/extract").json()["cached"]

stripes = np.zeros((40, 160, 3))
truth = [(226, 86, 60), (28, 52, 84), (245, 239, 224), (44, 142, 132)]
for i, c in enumerate(truth):
    stripes[:, i * 40:(i + 1) * 40] = c
flat = client.post(f"{api}/images", files={"file": ("stripes.png", png(stripes), "image/png")}).json()["palette"]
noise = client.post(f"{api}/images", files={"file": ("noise.png", png(np.random.default_rng(3).integers(0, 256, (120, 120, 3))), "image/png")}).json()["palette"]
bad = client.post(f"{api}/images", files={"file": ("notes.png", b"not an image", "image/png")})

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
        r = self.c.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})


fixer_app.LOG_PATH = Path(tmp) / "fixer.db"
fixer_app.app.state.transport = _Bridge(mock_vibecheck.app)
ev = {**mock_vibecheck.SAMPLE_EVENT, "score": mock_vibecheck.score("t1", mock_vibecheck.SAMPLE_EVENT["plan"]["palette"])}
fixer_client = TestClient(fixer_app.app)
panel = fixer_client.post("/hook", json=ev).json()
run = fixer_client.get("/runs").json()[0]["payload"]

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

# ============================================================ title page
P("PaletteAI", ParagraphStyle("t", parent=ss["Title"], fontSize=30, spaceBefore=90, textColor=colors.HexColor("#0d366b")))
P("Final Project Report", ParagraphStyle("st", parent=ss["Title"], fontSize=16))
P("CS698B Course Project &nbsp;|&nbsp; Self-dev: PaletteAI &nbsp;|&nbsp; Cross-dev: Palette Fixer &amp; Re-Scorer for VibeCheck",
  ParagraphStyle("c", parent=BODY, alignment=TA_CENTER, spaceAfter=30))
table([["#", "Name", "Roll No", "Email"],
       ["1", "Jagrab Jyoti Deuri", "265017", "jagrabjd26@iitk.ac.in"], ["2", "Neilan Ghosh", "265023", "neilang26@iitk.ac.in"],
       ["3", "Kamlesh Malviya", "265019", "kamleshm26@iitk.ac.in"], ["4", "Tarun Kumar", "265030", "tarunkumar26@iitk.ac.in"],
       ["5", "Sagnik Saha", "265026", "sagniks26@iitk.ac.in"]], [0.06, 0.36, 0.16, 0.42])
P("<b>Team name:</b> FORSAKEN (as printed in the proposal header; confirm before submission). "
  "<b>Cross-dev partner:</b> team \"rm -rf sleep\" (VibeCheck): Pranil, Niteesh, Sreejit, Neel, Ekansh.")
P("<b>How to read this report.</b> Part A is the self-dev project (sections 1-8). Part B is the cross-dev plugin (section 9). "
  "Sections 10-12 cover third-party code, AI usage and limitations. Appendix A is the complete OpenAPI document. "
  "All measured numbers were produced by running the submitted code when this PDF was built.")
story.append(PageBreak())

# ============================================================ 1 overview
h1("Part A: Self-dev project (PaletteAI)")
h1("1. Overview")
P("PaletteAI is a locally run web application for extracting, exploring, evaluating and organising colour palettes from images. "
  "A user uploads an image (or picks a built-in sample) and receives its dominant colours with their share of the image. "
  "The app then checks every colour pair against WCAG contrast thresholds, previews the image and palette under three colour-vision "
  "deficiencies, suggests colour harmonies, and lets the user save palettes and rate them. Ratings train a per-user model that ranks the "
  "library by similarity to what the user liked. The scope is palette discovery and accessibility; it does not edit images.")
table([["Component", "Summary", "Main files"],
       ["ETL", "Upload validation, normalisation, pixel sampling, CIELAB conversion, SQLite storage, content-hash de-duplication.", "etl.py, service.py, db.py"],
       ["AIML", "K-Means in CIELAB with silhouette-based choice of k and a fallback; feedback-trained preference ranking.", "clustering.py, ranking.py"],
       ["VIZ", "Swatches, composition / silhouette / ranking charts, contrast table, colour-vision previews, harmony view; light and dark themes.", "static/"],
       ["APP", "FastAPI server with a plain HTML/CSS/JavaScript single-page interface; runs offline on a laptop.", "main.py, run.py"],
       ["DEV", "Documented REST API (OpenAPI 3.1) and a plugin system for panels and extra routes.", "api.py, plugins.py, schemas.py"]],
      [0.12, 0.62, 0.26])

h1("2. Architecture")
P("One Python process serves both the REST API and the static front-end. The front-end talks to the server only through the public API, "
  "so everything the interface does is also available to other teams.")
table([["Layer", "Responsibility"],
       ["Browser (static/index.html, app.js, *.css)", "Five tabs: Extract, Library & ranking, Contrast checker, Harmony, Developers. No framework and no build step."],
       ["REST API (api.py, schemas.py)", "Routes under /api/v1; request validation and response models with Pydantic; uniform error body."],
       ["Service (service.py)", "Business logic shared by the API and by in-process plugins: extraction, library, feedback, ranking."],
       ["Engines (etl.py, clustering.py, color.py, ranking.py)", "Pure functions with no I/O, unit-tested in isolation."],
       ["Plugins (plugins.py)", "Loads local plugins from plugins/, calls registered webhook plugins, validates returned panels."],
       ["Storage (db.py)", "SQLite file data/paletteai.db plus normalised preview images in data/previews/."]], [0.36, 0.64])
P("<b>Run:</b> <font face='Courier'>python -m venv .venv</font>, <font face='Courier'>.venv\\Scripts\\python -m pip install -r requirements.txt</font>, "
  "<font face='Courier'>.venv\\Scripts\\python run.py</font>, then open http://127.0.0.1:8000. No internet access or external service is required.")

# ============================================================ 3 ETL
h1("3. Data component (ETL)")
h2("3.1 Source and pipeline")
P("The data source is user-generated: image uploads through the web form or the API, plus six sample images generated locally "
  "(sunset, forest, ocean, poster, logo with transparency, monochrome gradient) so the full workflow can be demonstrated offline. "
  "User feedback (save, like, dislike) is the second data stream.")
table([["Step", "What happens", "Limits / details"],
       ["1. Validate", "Reject empty files, oversize files, unsupported formats and files that fail Pillow's integrity check.",
        f"Max {etl.MAX_UPLOAD_BYTES // 1048576} MB, max {etl.MAX_PIXELS // 10**6} megapixels; formats {', '.join(sorted(etl.ALLOWED_FORMATS))}."],
       ["2. Hash", "SHA-256 of the uploaded bytes. If the same hash and colour-count setting already exist, the stored palette is returned.", "Response has cached = true."],
       ["3. Normalise", "Apply EXIF orientation, take the first frame of animations, convert to RGBA.", ""],
       ["4. Sample", "Downscale with nearest-neighbour (keeps true pixel colours), drop transparent pixels, take a deterministic random sample.",
        f"Longest side {etl.ANALYSIS_SIDE} px; alpha below {etl.ALPHA_THRESHOLD} ignored; up to {etl.MAX_SAMPLES} pixels; seed derived from the hash."],
       ["5. Transform", "Convert sampled sRGB pixels to CIELAB (D65).", "Perceptually more uniform than RGB for clustering."],
       ["6. Derive", "Cluster (section 4), compute coverage, colour names, feature vector, and WCAG contrast for every pair.", ""],
       ["7. Load", "Insert image and palette rows in one transaction and write a flattened JPEG preview.", f"Preview longest side {etl.DISPLAY_SIDE} px."]],
      [0.14, 0.50, 0.36])
h2("3.2 Persistent store (SQLite)")
table([["Table", "Key columns", "Purpose"],
       ["images", "id, content_hash (unique), filename, mime, width, height, size_bytes, has_alpha, transparent_fraction, source", "One row per distinct uploaded file."],
       ["palettes", "id, image_id, requested_k, k, method, silhouette, k_scores_json, fallback_used, reason, colors_json, features_json, accessibility_json",
        "Extraction result; unique per (image, requested_k)."],
       ["library", "user_id, palette_id, name, saved_at", "Per-user saved palettes."],
       ["feedback", "user_id, palette_id, value (+1 / -1), updated_at", "Like / dislike used by the ranking model."],
       ["plugins", "id, name (unique), description, hook_url, slots_json, enabled", "Registered webhook plugins."]], [0.13, 0.57, 0.30])
h2("3.3 Error handling")
P("Invalid input never enters the dataset: validation happens before any insert, and image and palette rows are written in a single transaction. "
  f"Example measured at build time: uploading a text file named notes.png returned HTTP {bad.status_code} with code "
  f"<font face='Courier'>{bad.json()['detail']['code']}</font> and message \"{escape(bad.json()['detail']['message'])}\".")
table([["HTTP", "Code", "Cause"], ["400", "empty_file", "Zero-byte upload."], ["413", "file_too_large / image_too_large", "Over the byte or pixel limit."],
       ["415", "unsupported_type / invalid_image", "Wrong format, corrupt or non-image data."], ["422", "fully_transparent", "No opaque pixels to analyse."],
       ["422", "validation_error / invalid_color", "Bad parameter, e.g. k outside 2-10 or malformed HEX."], ["404", "not_found", "Unknown palette, image, sample or plugin."],
       ["500", "processing_failed", "Unexpected failure during extraction; nothing is stored."]], [0.10, 0.38, 0.52])

# ============================================================ 4 AIML
h1("4. Adaptive component (AIML)")
h2("4.1 Dominant-colour clustering")
P("K-Means is implemented directly on NumPy (k-means++ seeding, 3 restarts, at most 40 Lloyd iterations, empty clusters re-seeded at the worst-fitted point). "
  f"In automatic mode the app evaluates k = {clustering.AUTO_K_RANGE.start} to {clustering.AUTO_K_RANGE.stop - 1} and keeps the k with the highest mean silhouette "
  "coefficient, computed on a subsample of up to 1000 pixels to keep the cost bounded.")
bullets([f"<b>Fallback.</b> If the best silhouette is below {clustering.MIN_CONFIDENT_SILHOUETTE} or the candidates differ by less than "
         f"{clustering.MIN_SILHOUETTE_SPREAD}, the result is declared inconclusive and k = {clustering.FALLBACK_K} is used. The interface says so.",
         f"<b>Merging.</b> In automatic mode, clusters closer than {clustering.MERGE_DELTA_E:g} Delta-E are merged so near-identical colours are not listed twice.",
         "<b>Few colours.</b> Images with three or fewer distinct colours skip clustering and use those colours directly.",
         f"<b>Manual mode.</b> The user may request k from {clustering.MANUAL_K_MIN} to {clustering.MANUAL_K_MAX}.",
         "<b>Determinism.</b> Sampling and seeding derive from the file hash, so the same file always gives the same palette."])
h2("4.2 Clustering behaviour on test cases (measured)")
rows = [["Image", "k", "Silhouette", "Fallback", "Palette (by coverage)", "Top colours"]]
for name, p in list(samples.items()) + [("4-colour stripes (synthetic)", flat), ("uniform RGB noise (synthetic)", noise)]:
    rows.append([name, str(p["k"]), "-" if p["silhouette"] is None else f"{p['silhouette']:.3f}", "yes" if p["fallback_used"] else "no",
                 strip([c["hex"] for c in p["colors"]], [c["share"] for c in p["colors"]]),
                 ", ".join(f"{c['hex']} {c['percent']:.0f}%" for c in p["colors"][:3])])
table(rows, [0.20, 0.05, 0.10, 0.09, 0.28, 0.28])
exact = {c["hex"] for c in flat["colors"]} == {"#%02X%02X%02X" % t for t in truth}
P(f"<b>Observations.</b> The synthetic stripe image has four known colours; the app recovered {'all four exactly' if exact else 'a different set'} "
  f"(k = {flat['k']}). Uniform noise has no real clusters (best silhouette {max(s['silhouette'] for s in noise['k_scores']):.2f}); "
  f"the fallback {'was triggered' if noise['fallback_used'] else 'was NOT triggered'} and the app reported: \"{escape(noise['reason'])}\" "
  f"The logo sample has a transparent background, which was ignored (k = {samples['logo']['k']}, "
  f"{samples['logo']['image']['transparent_fraction'] * 100:.0f}% of pixels transparent). "
  f"Re-submitting the sunset sample returned cached = {str(recached).lower()}, confirming de-duplication.")
h2("4.3 Preference ranking")
P("Each palette is reduced to eight features scaled to 0-1. For a user with liked set A and disliked set B, a palette x is scored as "
  "<b>score(x) = mean<sub>a in A</sub> K(x, a) - mean<sub>b in B</sub> K(x, b)</b>, where K is a Gaussian kernel "
  f"exp(-d<super>2</super> / 2s<super>2</super>) with s = {ranking.KERNEL_WIDTH} and d a weighted Euclidean distance. Scores lie in [-1, 1]. "
  "The weights adapt as well: w<sub>j</sub> = 1 + 4 |mean<sub>A,j</sub> - mean<sub>B,j</sub>|, normalised to mean 1, so features that separate "
  "a user's likes from dislikes count for more. With no feedback (cold start) every score is 0 and palettes are listed newest first. "
  "Each result carries a plain-language reason.")
table([["Feature", "Meaning"]] + [[k, {"mean_lightness": "Coverage-weighted mean L*.", "lightness_spread": "Weighted standard deviation of L*.",
                                        "mean_chroma": "Coverage-weighted mean chroma.", "max_chroma": "Chroma of the most vivid colour.",
                                        "contrast_spread": "Log of the highest contrast ratio between two palette colours.",
                                        "size": "Number of colours.", "warmth": "Chroma-weighted warm/cool balance of hues.",
                                        "hue_diversity": "Circular variance of hues (faded out for near-grey palettes)."}[k]] for k in ranking.FEATURE_KEYS],
      [0.28, 0.72])
h2("4.4 Ranking behaviour on a test scenario (measured)")
P("Profile \"demo\" saved the six sample palettes, then liked <b>sunset</b> and disliked <b>mono</b> and <b>forest</b>.")
before = {it["palette"]["image"]["filename"]: it["rank"] for it in cold["items"]}
rows = [["Rank after", "Palette", "Score", "Rank before (newest first)", "Feedback", "Reason shown to the user"]]
for it in warm["items"]:
    fn = it["palette"]["image"]["filename"]
    rows.append([str(it["rank"]), fn, f"{it['score']:+.2f}", str(before[fn]), it["palette"]["feedback"] or "-", " ".join(it["reasons"])])
table(rows, [0.09, 0.12, 0.08, 0.14, 0.10, 0.47])
wts = sorted(warm["model"]["feature_weights"].items(), key=lambda kv: -kv[1])
P(f"Model status changed from <i>{cold['model']['status']}</i> to <i>{warm['model']['status']}</i>. Learned feature weights: "
  + ", ".join(f"{warm['model']['feature_labels'][k]} {v:.2f}" for k, v in wts) + ". The unit tests additionally verify that reversing the "
  "feedback reverses the order and that one user's feedback does not affect another's ranking.")

# ============================================================ 5 VIZ
h1("5. Visualization component (VIZ)")
table([["View", "What it shows"],
       ["Extraction result", "Image preview; ordered swatch list with HEX, plain-language colour name and percentage; Save / Like / Dislike."],
       ["Composition chart", "Horizontal bars, one per colour, filled with that colour and labelled with HEX, name and share."],
       ["Colour-count chart", "Silhouette score for every candidate k, with the chosen k marked in text, plus the written reason."],
       ["Colour-vision previews", "Radio buttons switch the image between normal vision, protanopia, deuteranopia and tritanopia (Machado et al. 2009 matrices in linear RGB); a table lists each colour under each condition."],
       ["Contrast table", "Every colour pair with a text sample, ratio, and Pass / Fail for AA normal, AA large and AAA normal text."],
       ["Library & ranking", "Preference-model summary, learned feature-weight chart, diverging score chart (-1 to +1), ranked palette cards with reasons."],
       ["Contrast checker", "Any two colours: live preview, ratio, five WCAG checks with Pass / Fail."],
       ["Harmony", "Complementary, analogous, triadic, split-complementary, tetradic and monochromatic schemes for a base colour."],
       ["Plugin panels", "Panels contributed by plugins, rendered with the same components."]], [0.24, 0.76])
h2("5.1 Accessibility features")
bullets(["<b>Never colour alone:</b> every swatch carries its HEX value and a colour name; results use the words Pass / Fail with a tick or cross; rank and score are printed as numbers.",
         "<b>Charts are data tables:</b> each chart is an HTML table with caption, row and column headers, so it works in screen readers and text browsers.",
         "<b>Keyboard:</b> skip link, ARIA tab pattern with arrow / Home / End keys, visible focus ring on every control, focus moved to new results.",
         "<b>Screen readers:</b> labelled form controls, live region announcing status and errors, descriptive alt text listing dominant colours.",
         "<b>Contrast and themes:</b> light and dark themes with text colours chosen to meet WCAG AA; text on swatches switches between black and white for readability.",
         "<b>Other:</b> honours reduced-motion and forced-colours settings; layout reflows to phone width; print styles; a no-JavaScript notice pointing to the API."])
P("<b>Note for the team:</b> insert screenshots of the Extract, Library and Contrast views here before submission.", NOTE)

# ============================================================ 6 APP
h1("6. App component (APP)")
P("PaletteAI is packaged as a web application: a FastAPI server (Uvicorn) that serves a single-page interface written in plain HTML, CSS and JavaScript, "
  "with SQLite persistence. There is no build step and no client-side framework. Profiles are plain names (no passwords) that separate libraries and "
  "feedback, which is appropriate for a locally run demo. Input is validated both in the browser and on the server, every failure produces a readable "
  "message, and the built-in samples allow a complete demonstration without any network access.")

# ============================================================ 7 DEV
h1("7. Dev component (DEV)")
h2("7.1 Plugin system")
P("Another team can extend PaletteAI without modifying core files, and the extension appears inside the PaletteAI interface rather than as a separate app.")
table([["", "Local plugin", "Webhook plugin"],
       ["Form", "Folder plugins/<name>/plugin.py exposing register(host)", "Any HTTP service, any language"],
       ["Adds panels", "host.add_panel(slot, function)", "Returns a Panel from its hook URL"],
       ["Adds API routes", "host.add_router(router), mounted at /api/v1/ext/<name>/ and listed in /docs", "Runs its own server"],
       ["Data access", "host.api (palettes, ranking, contrast, harmony, CVD)", "REST API via api_base in the request"],
       ["Install", "Drop the folder in and restart", "POST /api/v1/plugins or the Developers tab"],
       ["Example shipped", "plugins/tints_and_shades", "examples/webhook_plugin.py"]], [0.16, 0.44, 0.40])
P("<b>Slots:</b> <font face='Courier'>palette.result</font> (under every palette) and <font face='Courier'>library</font> (Library tab). "
  "<b>Panel:</b> a title and up to 20 typed blocks (text, status, stat, swatches, table, bars). Blocks are data, never HTML, so plugins cannot inject script. "
  "<b>Isolation:</b> a plugin that raises, returns an invalid panel or exceeds 3 seconds is shown as \"unavailable\" and the page continues to work. "
  "The full guide with request and response examples is docs/PLUGINS.md.")
h2("7.2 REST API reference")
P(f"Generated from the application's OpenAPI {spec['openapi']} document ({spec['info']['title']} {spec['info']['version']}), which is served at /openapi.json, "
  "browsable at /docs and /redoc, saved as docs/openapi.json, and reproduced in full in Appendix A. "
  "All errors have the body {\"detail\": {\"code\": ..., \"message\": ...}}.")
rows = [["Method and path", "Summary", "Inputs", "Responses"]]
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        ins = [f"{p['name']} ({p['in']})" for p in op.get("parameters", [])]
        for ctype, body in (op.get("requestBody", {}).get("content") or {}).items():
            ref = body.get("schema", {}).get("$ref", "")
            ins.append(f"body: {ref.split('/')[-1] or ctype}")
        rows.append([f"{method.upper()} {path}", op.get("summary", ""), ", ".join(ins) or "-", ", ".join(op.get("responses", {}))])
table(rows, [0.36, 0.30, 0.22, 0.12])
for name, hook in spec.get("webhooks", {}).items():
    P(f"<b>Webhook <font face='Courier'>{name}</font>:</b> {escape(hook['post'].get('summary', ''))}. Request body PanelRequest; expected reply Panel.")
h2("7.3 Data models")
rows = [["Schema", "Fields"]]
for name, sch in sorted(spec["components"]["schemas"].items()):
    props = sch.get("properties")
    if props and not name.startswith(("HTTPValidation", "Validation", "Body_")):
        rows.append([name, ", ".join(props)])
table(rows, [0.24, 0.76])

# ============================================================ 8 testing
h1("8. Testing")
P(f"The suite uses pytest. Result at build time: <b>{escape(result_line.strip('= '))}</b> ({n_cases} test cases including parametrised ones). "
  "API tests use FastAPI's test client against a fresh temporary database per test, so they exercise the real HTTP layer, validation and SQLite storage.")
desc = {"test_core": "Unit tests: HEX parsing, WCAG contrast against known values, CIELAB reference values and round trip, CVD simulation, harmony, "
                     "ETL rejection and transparency handling, cluster-count recovery, fallback, determinism, ranking features and adaptivity.",
        "test_api": "End-to-end API tests: exact colour recovery, de-duplication, error codes with nothing stored, samples and previews, per-user library, "
                    "feedback changing and reversing the ranking, colour tools, local and webhook plugin life cycle, failure isolation, OpenAPI contents.",
        "test_palette_fixer": "Cross-dev plugin: CIEDE2000 against published reference pairs, HEX validation, swap selection, contrast protection, swap limit, "
                              "full hook round trip against the mock host, every fallback path, panel schema validity."}
table([["File", "Tests", "Coverage"]] + [[f"tests/{f}.py", str(len(v)), desc.get(f, "") + " Functions: " + ", ".join(v)] for f, v in tests.items()],
      [0.22, 0.07, 0.71])
P("Run with <font face='Courier'>.venv\\Scripts\\python -m pytest tests -q</font>. The interface was additionally checked by hand in a browser "
  "(extraction, bad-file error, library ranking, contrast checker, harmony, both themes, narrow width).")

# ============================================================ 9 cross-dev
story.append(PageBreak())
h1("Part B: Cross-dev project")
h1("9. Palette Fixer &amp; Re-Scorer (plugin for VibeCheck)")
P("Base project: <b>VibeCheck</b> by team \"rm -rf sleep\". Our plugin sits in VibeCheck's result slot. When a design check completes, it finds colours "
  "that are far from the team's signature palette, proposes replacements that are closer while keeping text readable, asks VibeCheck to score the proposed "
  "palette, and shows original and returned scores with an explanation of each swap. It recommends only and never edits a poster.")
P("<b>Integration status (must be updated by the team).</b> When this report was built, VibeCheck's published API was not available to us. The plugin is "
  "implemented and tested against an <b>assumed contract</b> and a mock host that we wrote (crossdev/palette_fixer/mock_vibecheck.py). Everything specific to "
  "VibeCheck is isolated in client.py. The mock's score is only closeness to the signature palette; it is not VibeCheck's scoring. Replace this note with the "
  "result of the integration test against the real VibeCheck instance.", NOTE)
table([["Component", "Implementation"],
       ["ETL", "Receives the check.completed event (plan palette, failure reasons, team, api_base); fetches the team's signature palette through VibeCheck's API; "
               "validates HEX values and reports rejected ones; converts to CIELAB; records each run (input, suggestions, re-score response, timing) in a local SQLite file, readable at GET /runs."],
       ["AIML", f"CIEDE2000 distance (implemented from Sharma et al. 2005 and verified against published reference pairs) to the nearest signature colour. A colour is off-brand above "
                f"{fixer.MISMATCH_DELTA_E:g} Delta-E or when flagged by VibeCheck. At most 3 swaps per fix, worst mismatches first, replacement colours kept distinct. "
                "A swap is rejected if the text/background contrast would fall below WCAG AA (4.5:1), or below its original value if that was already under AA; "
                "in that case the signature hue at the original lightness is tried instead. The new score is whatever VibeCheck returns; no improvement is predicted."],
       ["VIZ", "A panel with original and suggested palettes side by side (HEX labels), a swap table with before/after distances and reasons, a contrast statement in words, and both scores."],
       ["APP", "Runs inside the host app: VibeCheck imports the plugin and calls handle_event(event, team_palette, rescore) with its own functions, so there is no "
               "second app, window or server, and the panel is drawn in VibeCheck's result page. If VibeCheck's plugin interface is webhook-based, the same function is "
               "exposed as POST /hook (2-second time-outs per call). Any failure produces a clear fallback panel and never crashes the host."],
       ["DEV", "Own OpenAPI docs at /docs, sample event, README with the assumed contract and where to change it, automated tests."]], [0.12, 0.88])
h2("9.1 Round trip against the mock host (measured)")
fix = run["fix"]
P(f"Sample event: team \"{ev['team']['name']}\", plan palette {', '.join(ev['plan']['palette'])}, text {ev['plan']['foreground']} on {ev['plan']['background']}. "
  f"Signature palette: {', '.join(run['signature'])}.")
table([["Replace", "With", "Delta-E before", "Delta-E after", "Kind"]]
      + [[s["from"], s["to"], str(s["delta_e_before"]), str(s["delta_e_after"]), s["kind"]] for s in fix["swaps"]], [0.16, 0.16, 0.16, 0.16, 0.36])
table([["", "Original", "Suggested"],
       ["Palette", strip(fix["original"], [1 / len(fix["original"])] * len(fix["original"])), strip(fix["proposed"], [1 / len(fix["proposed"])] * len(fix["proposed"]))],
       ["Mean Delta-E to signature", str(fix["mean_delta_e_before"]), str(fix["mean_delta_e_after"])],
       ["Text contrast", f"{fix['contrast']['before']}:1", f"{fix['contrast']['after']}:1 ({'passes' if fix['contrast']['passes_aa'] else 'fails'} AA)"],
       ["Score returned by the mock host", str(ev["score"]), str(run["rescore"]["score"])]], [0.34, 0.33, 0.33])
P(f"Colours already close to the signature palette were left unchanged ({fix['mismatches']} of {len(fix['original'])} judged off-brand). "
  f"The panel returned to the host contained {len(panel['blocks'])} blocks and validates against the Panel schema.")

# ============================================================ 10-12
h1("10. Third-party code, libraries and services")
P("No credit is claimed for the items below. No external service, data feed or LLM API is used by either system at run time.")
table([["Item", "Use", "Type"],
       ["FastAPI, Starlette, Pydantic, Uvicorn, python-multipart", "Web framework, validation, server, form uploads; OpenAPI generation and the /docs pages", "Library"],
       ["Pillow", "Image decoding, resizing, JPEG/PNG encoding, sample-image drawing", "Library"],
       ["NumPy", "Array arithmetic for colour conversion and clustering", "Library"],
       ["httpx", "Outgoing webhook calls; HTTP client in the cross-dev plugin", "Library"],
       ["SQLite (Python sqlite3)", "Persistent storage", "Library"],
       ["pytest", "Test runner", "Tool"],
       ["ReportLab, pypdf", "Building this PDF; reading the proposal PDF", "Tool"],
       ["Machado, Oliveira & Fernandes (2009)", "Colour-vision-deficiency simulation matrices", "Published data"],
       ["WCAG 2 contrast formula; CIELAB; CIEDE2000 (Sharma, Wu & Dalal 2005)", "Standard formulas implemented by us from the specifications", "Standard"],
       ["Uiverse.io design by zl306", "Pixel-art light/dark theme switch (CSS and embedded images), adapted", "Third-party CSS"],
       ["Uiverse.io design by njesenberger", "3D \"Extract palette\" button, adapted", "Third-party CSS"],
       ["Uiverse.io design by Admin12121", "Animated cube logo, adapted", "Third-party CSS"]], [0.40, 0.44, 0.16])
P("<b>Developed for this project:</b> the ETL pipeline, K-Means and silhouette implementation, k selection and fallback, colour conversions, contrast, "
  "colour-vision simulation code, harmony, colour naming, preference-ranking model, REST API, plugin system, the web interface, the cross-dev plugin "
  "(including CIEDE2000 and the swap logic) and the tests. These were developed with AI assistance as described in section 11.")

h1("11. AI usage")
P("<b>TO BE WRITTEN BY THE TEAM.</b> The course policy requires this section to be written by humans, not by AI, and to be detailed rather than a short "
  "paragraph. It has deliberately been left for the team. Cover at least: (1) every way AI was used (planning, proposal, coding, testing, interface design, "
  "this report's other sections); (2) which AI tools and models; (3) how access was obtained (free or paid plan); (4) your experience: hallucinations, "
  "intent mismatches, token or usage limits, what had to be corrected, what you verified yourselves, and what you would do differently.", NOTE)

h1("12. Limitations and future work")
bullets(["The cross-dev plugin has not yet been run against the real VibeCheck; endpoint paths, event fields and panel format are assumptions isolated in one file.",
         "Profiles have no authentication. This is acceptable for a local demo and unsuitable for a shared deployment.",
         "The ranking model is evaluated on scenario tests, not on a user study; with very little feedback its scores are necessarily coarse.",
         f"The silhouette threshold for the fallback ({clustering.MIN_CONFIDENT_SILHOUETTE}) was set empirically: uniform noise scores 0.29-0.35, while the sample images score 0.57 or more.",
         "Colour-vision previews are simulations of complete dichromacy and do not model milder anomalies.",
         "Palettes cannot be renamed or deleted from the interface, and there is no export (CSS variables / JSON) yet.",
         "Possible next steps: warnings for palette colours that become indistinguishable under a colour-vision deficiency, suggested accessible replacements for failing pairs, palette export."])

# ============================================================ appendix
story.append(PageBreak())
h1("Appendix A. OpenAPI document")
P(f"Complete OpenAPI {spec['openapi']} document as served by the application at /openapi.json (also saved as docs/openapi.json).")
lines = []
for ln in json.dumps(spec, indent=1, ensure_ascii=True).splitlines():
    indent = len(ln) - len(ln.lstrip())
    lines += textwrap.wrap(ln, 165, subsequent_indent=" " * (indent + 2)) or [""]
for i in range(0, len(lines), 110):
    story.append(Preformatted("\n".join(lines[i:i + 110]), MONO))


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(18 * mm, 10 * mm, "PaletteAI - CS698B Final Project Report")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


out = ROOT / "report.pdf"
SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                  title="PaletteAI - Final Project Report", author="Team FORSAKEN").build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", out, "|", result_line.strip())
