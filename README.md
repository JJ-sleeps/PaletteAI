# PaletteAI

A local web app for extracting, exploring, evaluating and organising colour
palettes from images. CS698B course project.

## Run it

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

```bash
.venv\Scripts\python run.py
```

Open <http://127.0.0.1:8000>. API reference: <http://127.0.0.1:8000/docs>.
(On macOS/Linux use `.venv/bin/python`.) No internet connection or external
service is needed; twelve sample images are ready on first start (nine drawn in code,
three IIT Kanpur photos shipped in `paletteai/sample_photos/`).

Run the tests:

```bash
.venv\Scripts\python -m pytest tests -q
```

## The five components

| Component | What it does | Where |
|---|---|---|
| **ETL** | Validates uploads (type, 10 MB limit, integrity), fixes orientation, takes the first frame, ignores transparent pixels, resizes, samples up to 5000 pixels, converts to CIELAB. Stores image metadata, palettes, accessibility results, saved palettes and feedback in SQLite. A SHA-256 content hash skips repeat extraction. Bad files get a clear error and store nothing. | `paletteai/etl.py`, `service.py`, `db.py` |
| **AIML** | K-Means (k-means++ seeding, written on NumPy) in CIELAB. Tries k = 3-8 and picks the best silhouette score, with a fallback to 5 when the score is inconclusive; near-identical clusters are merged. A per-user preference model ranks palettes by similarity to liked and dissimilarity from disliked palettes over 8 features, and re-weights features by how well they separate likes from dislikes. | `paletteai/clustering.py`, `ranking.py` |
| **VIZ** | Labelled swatches with HEX, name and coverage; composition, silhouette and ranking bar charts that are also real data tables; WCAG contrast table with Pass/Fail text; protanopia / deuteranopia / tritanopia previews of the image and palette; harmony suggestions. Keyboard tabs, skip link, visible focus, live status messages, light and dark themes, no colour-only information. | `paletteai/static/` |
| **APP** | FastAPI backend, plain HTML/CSS/JS frontend (no build step), SQLite. | `paletteai/main.py`, `run.py` |
| **DEV** | REST API with generated OpenAPI docs at `/docs` and `/openapi.json`. Plugins add panels and API routes without touching the core. | `paletteai/api.py`, `plugins.py`, `docs/PLUGINS.md` |

## Layout

```
paletteai/        application package
  color.py        colour conversions, WCAG contrast, CVD simulation, harmony
  etl.py          upload validation and normalisation
  clustering.py   K-Means + silhouette k selection
  ranking.py      feedback-based preference model
  service.py      business logic (extract, library, feedback, ranking)
  api.py          REST endpoints          schemas.py   request/response models
  plugins.py      plugin loader and panel collection
  static/         the web interface
plugins/          local plugins (tints_and_shades is the integration example)
examples/         webhook plugin example
tests/            pytest suite (unit + end-to-end API)
docs/PLUGINS.md   guide for cross-dev teams
data/             created at run time: SQLite database, previews, sample images
```

## Third-party code

FastAPI, Uvicorn, Pydantic, python-multipart (web framework), Pillow (image
decoding), NumPy (array maths), httpx (webhook calls), pytest (tests). The
colour-vision matrices are from Machado, Oliveira and Fernandes (2009). The
pixel-art theme switch (CSS and images) is adapted from a Uiverse.io design by
zl306, the "Extract palette" 3D button from one by njesenberger, the animated cube
logo from a loader by Admin12121, the extraction loader from one by Donewenfu, and
the pixel-art name field from an input by xbeat_5120 (all MIT). The K-Means,
silhouette, colour conversion, contrast, harmony and ranking code is written for
this project.

### Sample photos

The three IIT Kanpur samples are photos from Wikimedia Commons, centre-cropped and
resized to 480 × 320. Each keeps its original licence:

| Sample | Original | Author | Licence |
|---|---|---|---|
| IITK Library | [P.K.kelkar library iit kanpur.jpg](https://commons.wikimedia.org/wiki/File:P.K.kelkar_library_iit_kanpur.jpg) | Lalujbad | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |
| IITK Computer Centre | [Computer Center at IIT Kanpur India.jpg](https://commons.wikimedia.org/wiki/File:Computer_Center_at_IIT_Kanpur_India.jpg) | Dr Vinamra | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
| IITK Hall 2 | [Hall 2 IIT Kanpur.jpg](https://commons.wikimedia.org/wiki/File:Hall_2_IIT_Kanpur.jpg) | Vaibhav.jndl | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |
