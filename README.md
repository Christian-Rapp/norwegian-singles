# Singles — Norwegian Singles Training Dashboard (local Mac app)

A diagnostic dashboard for Norwegian Singles training (Sirpoc method + Bakken's
lactate-guided threshold model). Runs entirely on your Mac: no accounts, no
cloud, your data never leaves the machine.

**Guiding principle:** pace guides progress, heart rate guides execution.

## Run it

**Easiest:** double-click `run.command`. It opens Terminal and starts the app.

**Or in Terminal:**

```bash
git clone <repo-url> norwegian-singles
cd norwegian-singles
python3 app.py
```

Then open **http://localhost:8000** in your browser.

Requirements: macOS with Python 3 (preinstalled). No pip installs, no
dependencies — everything is Python standard library.

To stop: Ctrl-C in the Terminal window (or just close it).

## What it does

- **Upload** `.fit` / `.gpx` / `.tcx` files (or a `.zip` of them, e.g. a Strava
  bulk export) via the Upload button.
- **Automatic workout classifier** — uses your manual laps as the reps when
  you lap the workout; otherwise detects work bouts from the pace stream
  (auto 1 km / 1 mi laps are ignored). Snaps track reps to nominal
  distances (6×1200m, not 6×1.26k), and classifies each run as
  threshold / easy / long run / race.
- **Week view** — day-by-day calendar (e.g. "6.2 mi easy", "8x800m · 8.0 mi total"), total time, quality minutes, threshold share
  with the method's 20–25% target band (30% caution line).
- **Workout detail** — rep table, HR execution gauge against your LTHR, and an
  automatic verdict (in zone / drifted hot / overcooked).
- **Progression** — rep pace over time colored by rep length (400 m red →
  2 mi blue) with a rolling 4-week training VDOT overlay and race VDOTs,
  quality minutes/week, volume, threshold-share history, and a current
  training VDOT gauge with estimated 2 mile / 5k / 10k / half times.
- **Race engine** — workout-derived LT pace (Bakken duration factors) vs
  race-predicted LT step line, race execution vs pre-race training prediction,
  and a read-only advisory suggested pace. Suggestions never change anything.
  Races at non-standard distances convert through VDOT (Daniels–Gilbert);
  races under 3 km are compared on VDOT but never set LT.
- **Strava** — Settings → Strava: enter your Strava API app's Client ID and
  Secret once (set its Authorization Callback Domain to `localhost`), click
  Connect Strava, then Import. Tokens are kept locally and refreshed
  automatically; imports pull new runs and backfill laps.

## Settings worth setting first

1. **LTHR** (lactate threshold HR) — unlocks execution verdicts and the HR gauge.
2. **Max HR** — sets the easy cap (auto = 70% of max, editable).
3. The **Bakken Golden-Zone factors** are tunable estimates, not physiological
   facts — see "About the zones" in Settings (credit: mariusbakken.com/vdot.html).

## Your data

Everything lives in the `data/` folder next to the app:

- `data/dashboard.db` — SQLite database (activities, weeks, settings).
- `data/uploads/` — your original uploaded files (kept so classification can
  be re-run after settings changes).

Back up `data/` if you care about it. Delete it to start fresh. It is
git-ignored, so your training data and Strava tokens never get committed.

## Files

- `app.py` — the server (stdlib `http.server` + JSON API).
- `fitparse_min.py` — minimal FIT parser (records, laps, session).
- `parsers.py` — FIT/GPX/TCX/ZIP → normalized track points.
- `classifier.py` — stream-based bout detection, track snapping, classification.
- `analysis.py` — week aggregates, progression series, LT/race engine.
- `db.py` — SQLite storage.
- `strava.py` — minimal Strava API import.
- `static/` — the web UI (vanilla JS, inline SVG charts, no CDN).
