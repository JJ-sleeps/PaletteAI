# Palette Fixer & Re-Scorer (cross-dev plugin for VibeCheck)

Our cross-dev contribution to team "rm -rf sleep"'s app, VibeCheck. It receives
VibeCheck's `check.completed` event, suggests colour swaps that move a design's
palette towards the team's signature palette without hurting text contrast,
asks VibeCheck to re-score the suggestion, and returns a panel for VibeCheck's
result slot. It recommends only; it never edits a poster.

## Status: built against an ASSUMED contract

VibeCheck's published API was not available when this was written. Everything
that depends on it is isolated in **`client.py`** (endpoint paths, event field
names, registration body). When their OpenAPI document arrives, update that
file and the panel layout in `app.py::build_panel` if their Panel schema
differs. The colour engine (`fixer.py`) does not depend on VibeCheck at all.

| Assumption | Where to change it |
|---|---|
| Event fields: `check_id`, `team.id`, `api_base`, `plan.palette`, `plan.foreground/background`, `score`, `failures[].color` | `client.py::parse_event` |
| `GET {api_base}/teams/{team_id}/palette` returns `{"colors": [...]}` | `PALETTE_PATH`, `team_palette` |
| `POST {api_base}/score` with `{team_id, palette, check_id}` returns `{"score": n}` | `SCORE_PATH`, `rescore` |
| Panel format `{title, blocks[]}` (same block types as PaletteAI's) | `app.py::build_panel` |

## Two ways to attach it to VibeCheck

The course brief says a cross-dev extension must work inside the base app, not as a
separate app. Which of these applies depends on VibeCheck's plugin interface.

**A. In-app (preferred; no second server).** VibeCheck imports the plugin and calls it
with its own functions:

```python
from crossdev.palette_fixer.app import handle_event

panel = handle_event(event, team_palette=get_team_palette, rescore=score_palette)
# get_team_palette(team_id) -> list of HEX colours
# score_palette(team_id, palette, check_id) -> {"score": number}
```

VibeCheck then draws `panel` in its result page. Nothing else needs to run.

**B. Webhook.** If VibeCheck only supports webhook plugins, run the service below and
register `http://127.0.0.1:8010/hook`. The panel is still drawn inside VibeCheck; the
service has no interface of its own.

Either way, demo it inside the real VibeCheck. The mock below is for development only.

## Try it without VibeCheck

Three terminals, from the project root:

```bash
.venv\Scripts\python -m crossdev.palette_fixer.mock_vibecheck
```

```bash
.venv\Scripts\python -m crossdev.palette_fixer.app
```

```bash
.venv\Scripts\python -m crossdev.palette_fixer.mock_vibecheck --send
```

The third command fires a sample `check.completed` event and prints the panel.
`GET http://127.0.0.1:8010/runs` shows the locally recorded runs;
`http://127.0.0.1:8010/docs` documents the plugin's own endpoints.

## Components

- **ETL:** parses the event, fetches the signature palette, validates HEX values, converts to CIELAB, records every run (input, suggestions, re-score response) in `data/fixer_log.db`.
- **AIML:** CIEDE2000 nearest-signature matching; swaps only colours more than 10 dE off-brand (or flagged by VibeCheck), at most 3 per fix, keeps colours distinct, and rejects any swap that would drop the text/background contrast below WCAG AA (or below its original value if it was already under AA). If the signature colour itself fails that test it tries the signature hue at the original lightness.
- **VIZ:** original and suggested palettes side by side with HEX labels, a swap table with distances and reasons, contrast status in words, original and returned scores.
- **APP:** FastAPI service, `POST /hook`, 2-second time-outs on each call to VibeCheck, clear fallback panels when anything is unavailable.
- **DEV:** `/docs` for the plugin, sample event in `mock_vibecheck.py`, tests in `tests/test_palette_fixer.py`.

The mock's score is only "closeness to the signature palette". It is not
VibeCheck's scoring and must not be presented as such.
