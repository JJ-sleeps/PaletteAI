# PaletteAI demo script (about 5 minutes)

The rubric rewards one integrated system, not five separate features. So the demo
tells one story: an image comes in, gets analysed, the app learns what you like,
and another team's code plugs into it. Each step names the component it proves.

## Before the slot (10 minutes earlier)

1. Start the app with `.venv\Scripts\python run.py` and open http://127.0.0.1:8000.
   Press **Ctrl+Shift+R** once so the browser loads the latest files.
2. Use a fresh profile name for the demo, for example `demo1`, so the ranking
   starts empty and the class sees it learn.
3. Keep a second tab open on http://127.0.0.1:8000/docs.
4. Have one image of your own ready on the desktop (a poster or a photo).
5. Run `.venv\Scripts\python -m pytest tests -q` once. If someone asks
   "does it work?", the answer is "49 passed".

## The run

| Time | Do this | Say this (in your own words) | Shows |
|---|---|---|---|
| 0:00 | Type `demo1` in **Your name**, press **Use**. | Each profile has its own library and its own preference model. | APP |
| 0:20 | Upload your own image. The loader appears, then the palette. | We validate the file, fix orientation, ignore transparent pixels, sample up to 5000 pixels and convert to CIELAB. The result is stored in SQLite under a SHA-256 hash. | ETL |
| 0:50 | Upload the same image again. The result line says it was loaded from the database. | Same content, same hash, so there's no second extraction. | ETL |
| 1:05 | Click **IITK Computer Centre**. Automatic gives 3 colours. Choose **6** and extract again. | K-Means in CIELAB tries k = 3 to 8 and picks the best silhouette score. On a busy photo it prefers a few broad groups; fixing k brings back the bougainvillea pinks and the greens. | AIML |
| 1:45 | Scroll to the silhouette chart under the result. | This is the score for every k we tried. The chart is also a real table, so screen readers get the numbers. | AIML + VIZ |
| 2:05 | Pick a colour-vision preview: **Deuteranopia**, then **Protanopia**. | The image and the palette as people with colour-vision deficiency see them (Machado et al. 2009). | VIZ |
| 2:25 | Open **Contrast checker**, try a light colour on white. It shows **Fail** in words, not only in red. | WCAG 2 thresholds, and colour is never the only signal. | VIZ |
| 2:45 | Open **Library & ranking**. Like 2 colourful palettes, dislike **Monochrome**. | The ranking re-orders right away. Each card explains why, and the feature weights show what the model learned. | AIML |
| 3:30 | Press **Tab** through the page without the mouse; switch the theme. | Keyboard navigation, visible focus, light and dark themes, reduced-motion support. | VIZ |
| 3:50 | In the `/docs` tab, run `GET /api/v1/samples` with **Try it out**. | Every feature in the UI is a documented REST endpoint (OpenAPI 3.1). | DEV |
| 4:10 | Back on a palette, scroll to the **Tints & shades** plugin panel. | Plugins add panels and routes without touching the core. This is the interface team rm -rf sleep built on. | DEV |
| 4:30 | Hand over to the cross-dev demo (next section). | | Cross-dev |

## Cross-dev demo: Palette Fixer inside VibeCheck

Our plugin runs as a small service; VibeCheck calls it after every check and draws the panel it returns in
VibeCheck's own result page. Three programs run at once, each on its own port:

| Program | Port | Started from |
|---|---|---|
| PaletteAI | 8000 | this folder |
| Palette Fixer (our plugin) | 8010 | this folder |
| VibeCheck (team rm -rf sleep) | 8030 | their folder (its default is 8000, which PaletteAI uses) |

### Before the slot

1. **Check VibeCheck has the plugin fix.** In their `backend/vibe/main.py`, `create_check` must call
   `await run_in_threadpool(plugins.run_plugins, ...)`. Without it our panel says it couldn't get the team's palette.
2. **Start VibeCheck on port 8030**, from their folder:
   ```powershell
   .venv\Scripts\python -m uvicorn vibe.main:app --app-dir backend --port 8030
   ```
3. **Start our plugin**, from this folder:
   ```powershell
   .venv\Scripts\python -m crossdev.palette_fixer.app
   ```
   Check http://127.0.0.1:8010/health shows `"status": "ok"`.
4. **Register the plugin once** (it stays registered). Either on VibeCheck's **Plugins** page (name
   `Palette Fixer & Re-Scorer`, URL `http://127.0.0.1:8010/hook`, slot **result**), or:
   ```powershell
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8030/plugins -ContentType "application/json" -Body '{"name":"Palette Fixer & Re-Scorer","url":"http://127.0.0.1:8010/hook","slot":"result","author_team":"PaletteAI"}'
   ```
5. **Do one practice check** with real CLIP on the demo laptop and confirm the panel shows a
   "Score with these swaps" number. If it says "No re-score ... in time", the re-score took over VibeCheck's
   5-second limit; the swaps still show.
6. Have one off-brand poster ready (colours far from the team's usual ones) and one on-brand poster.

### The run (about 2 minutes)

| Do this | Say this |
|---|---|
| In VibeCheck, run **Check my idea** with the off-brand poster. | VibeCheck says it's off-brand. Our plugin reacts to that check automatically. |
| Scroll to **Palette Fixer & Re-Scorer** under the result. | It found the colours furthest from the team palette using CIEDE2000, swapped at most 3, and kept text readable (contrast shown in words). |
| Point at **VibeCheck score now** vs **Score with these swaps**. | We recoloured the poster with the swaps and asked VibeCheck to score it again. That number is VibeCheck's own, not our guess. |
| Point at the bar chart. | Average distance from the team palette, before and after. |
| Open VibeCheck's **History**. | The re-score shows as its own check, marked "Palette Fixer (re-score)". Our plugin skips its own checks, so it never loops. |
| Run the on-brand poster. | Few or no swaps: the plugin doesn't change what's already on-brand. |

### If something goes wrong

- **Panel says it couldn't get the team's palette:** VibeCheck is missing the plugin fix (step 1).
- **No panel at all:** the plugin isn't running or isn't registered; check http://127.0.0.1:8010/health and VibeCheck's Plugins page.
- **"No re-score":** VibeCheck took too long; the swaps and contrast check are still valid.
- **Need to show what happened:** http://127.0.0.1:8010/runs lists every event the plugin received and what it returned.

## If something goes wrong

- **Page looks old after a change:** Ctrl+Shift+R.
- **Upload fails:** use the sample images; they are stored in the project and need no internet.
- **A question about the AI parts:** the clustering and ranking are our own NumPy code, not a library call and not an LLM. The report's section 10 has the measured results.

## Questions to be ready for

- *Why CIELAB and not RGB?* Distances in CIELAB match how different colours look; in RGB they don't.
- *Why silhouette for choosing k?* It scores how well each pixel fits its own cluster compared with the nearest other one, so no labels are needed. When the scores are too close to call, we fall back to k = 5.
- *How does the ranking learn?* Each palette becomes 8 features (lightness, chroma, contrast spread, palette size and others). The score is similarity to liked palettes minus similarity to disliked ones, and features that separate your likes from your dislikes get more weight.
- *What did you use AI for?* Answer from your own section 13. Don't improvise.
