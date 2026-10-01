"""Workout classifier: stream-based work-bout detection, track snapping,
classification (race/long/threshold/easy), template matching, HR verdicts.

Pure functions operating on normalized activities (see parsers.py).
Settings passed in as a dict: lthr, max_hr, easy_cap_hr.
"""

import math
import re
from bisect import bisect_left, bisect_right

# ---------- pace helpers ----------

def _smoothed_pace(points, window_s=15, cap=1200.0):
    """Per-point pace (s/km) via central difference over +/- window_s.

    Returns list aligned with points. Standing still -> cap."""
    n = len(points)
    out = [cap] * n
    for i, p in enumerate(points):
        t0, d0 = p["t"], p["dist"]
        # find point >= window_s before and after
        a = i
        while a > 0 and points[a]["t"] > t0 - window_s:
            a -= 1
        b = i
        while b < n - 1 and points[b]["t"] < t0 + window_s:
            b += 1
        dt = points[b]["t"] - points[a]["t"]
        dd = points[b]["dist"] - points[a]["dist"]
        if dt >= 2 * window_s * 0.7 and dd > 3:
            out[i] = min(cap, 1000.0 * dt / dd)  # s per km
        elif dd <= 3 and dt > 5:
            out[i] = cap
    return out


def timer_time(points, pause_gap=10):
    """Seconds the watch was running: elapsed minus pauses. A paused watch
    stops recording, so a pause is a gap in the samples (> pause_gap s);
    standing still with the timer running keeps recording and counts."""
    t = 0.0
    for p, q in zip(points, points[1:]):
        dt = q["t"] - p["t"]
        if 0 < dt <= pause_gap:
            t += dt
    return t


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


# ---------- work-bout detection ----------

def _easy_pace(pace):
    """Easy/recovery pace of the run (s/km). Upper quartile of moving pace
    rather than the median, since in rep-heavy sessions the work makes up
    half the moving time and would drag the median toward rep pace."""
    moving = sorted(p for p in pace if p < 900)
    if not moving:
        return None
    return moving[int(0.75 * (len(moving) - 1))]


def _make_bout(points, a, b, baseline):
    dur = points[b]["t"] - points[a]["t"]
    dist = points[b]["dist"] - points[a]["dist"]
    hrs = [points[k]["hr"] for k in range(a, b + 1) if points[k]["hr"] is not None]
    return {
        "start_i": a, "end_i": b,
        "start_t": points[a]["t"], "end_t": points[b]["t"],
        "duration_s": dur, "distance_m": dist,
        "avg_hr": sum(hrs) / len(hrs) if hrs else None,
        "max_hr": max(hrs) if hrs else None,
        "rel_pace": 1000.0 * dur / dist / baseline if dist else None,
    }


def detect_bouts(points, pace=None):
    """Detect work bouts from pace streams. Returns list of dicts:
    {start_i, end_i, duration_s, distance_m, avg_hr}."""
    if pace is None:
        pace = _smoothed_pace(points)
    baseline = _easy_pace(pace)
    if baseline is None:
        return []
    enter = 0.88 * baseline
    # Hysteresis: once in a bout, stay in it until pace is clearly off, so a
    # rep that fades (heat, hills) isn't chopped into fragments.
    leave = 0.94 * baseline
    n = len(points)
    # candidate runs
    runs = []
    i = 0
    while i < n:
        if pace[i] < enter:
            j = i
            while j < n and pace[j] < leave:
                j += 1
            dur = points[j - 1]["t"] - points[i]["t"]
            if dur >= 45:
                runs.append((i, j - 1))
            i = j
        else:
            i += 1
    # merge runs separated by < 25 s
    merged = []
    for a, b in runs:
        if merged and points[a]["t"] - points[merged[-1][1]]["t"] < 25:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    raw = _smoothed_pace(points, window_s=3)
    bouts = []
    for a, b in merged:
        # Refine edges on near-raw pace. The 15 s smoothing blurs a standing
        # start/stop by ~10 s each side (~10% of an 800), and the hysteresis
        # above can let a bout start on the accelerating jog.
        core = _median(pace[a:b + 1])
        while a < b and raw[a] > 1.3 * core:
            a += 1
        while a > 0 and raw[a - 1] <= 1.08 * core:
            a -= 1
        while b > a and raw[b] > 1.3 * core:
            b -= 1
        while b < n - 1 and raw[b + 1] <= 1.08 * core:
            b += 1
        dur = points[b]["t"] - points[a]["t"]
        dist = points[b]["dist"] - points[a]["dist"]
        if dur > 900 or dist < 300:
            continue
        bouts.append(_make_bout(points, a, b, baseline))
    return bouts


AUTO_LAP_M = (1000, 1609.34)


def _is_auto_laps(dists):
    """Device auto-laps: every lap but the last the same 1 km / 1 mi."""
    full = dists[:-1]
    for d in AUTO_LAP_M:
        if all(abs(x - d) / d <= 0.03 for x in full):
            return True
    return False


def bouts_from_laps(points, pace, laps):
    """Work bouts from the athlete's own laps: the laps run well under easy
    pace. None when the laps carry no workout structure (no laps, a single
    lap, or auto-laps), so the caller falls back to detect_bouts."""
    ts = [p["t"] for p in points]
    ranges = []
    for lap in laps or []:
        s, e = lap.get("start_t"), lap.get("end_t")
        if s is None or e is None or e <= s:
            continue
        a = bisect_left(ts, s)
        b = min(bisect_right(ts, e), len(points)) - 1
        if b > a:
            ranges.append((a, b))
    if len(ranges) < 3:
        return None
    if _is_auto_laps([points[b]["dist"] - points[a]["dist"] for a, b in ranges]):
        return None
    baseline = _easy_pace(pace)
    if baseline is None:
        return None
    bouts = []
    for a, b in ranges:
        dur = points[b]["t"] - points[a]["t"]
        dist = points[b]["dist"] - points[a]["dist"]
        # >= 150 m keeps a closing 200 but not a stray lap-button press
        if dist >= 150 and dur > 0 and 1000.0 * dur / dist < 0.88 * baseline:
            bouts.append(_make_bout(points, a, b, baseline))
    return bouts


# ---------- track detection & snapping ----------

TRACK_REPS = [400, 800, 1000, 1200, 1600, 2000, 2400, 3000, 3200, 4000, 5000]
RACE_DISTANCES = [5000, 10000, 21097.5, 42195]


def _bbox_diagonal_m(points):
    lats = [p["lat"] for p in points if p.get("lat") is not None]
    lons = [p["lon"] for p in points if p.get("lon") is not None]
    if len(lats) < 10:
        return None
    lat0 = sum(lats) / len(lats)
    # meters per degree approx
    dy = (max(lats) - min(lats)) * 111320.0
    dx = (max(lons) - min(lons)) * 111320.0 * math.cos(math.radians(lat0))
    return math.hypot(dx, dy)


def _laps_small_area(points, dist_m):
    """GPS trace stays in a track-sized area while covering many times its
    diagonal, i.e. running laps."""
    diag = _bbox_diagonal_m(points)
    return diag is not None and diag <= 300 and dist_m >= 8 * diag


def detect_track(points, bouts):
    """True when the work bouts look like track running (spec 4.3b).

    Uses GPS from the work bouts only (warmup/cooldown may roam elsewhere):
    the bout trace must stay in a compact area while the bout distance is
    many times the area's diagonal, and bout distances must cluster near
    multiples of 400 m."""
    if len(bouts) < 2:
        return False
    bout_pts = []
    for b in bouts:
        bout_pts.extend(points[b["start_i"]:b["end_i"] + 1])
    if not _laps_small_area(bout_pts, sum(b["distance_m"] for b in bouts)):
        return False
    # bout distances cluster near multiples of 400 m within 10%
    near = 0
    for b in bouts:
        mult = round(b["distance_m"] / 400.0)
        if mult >= 1 and abs(b["distance_m"] - mult * 400) / (mult * 400) <= 0.10:
            near += 1
    return near >= max(2, len(bouts) // 2)


def snap_bout_distance(dist_m):
    """Snap to nearest standard track rep within 10%. Returns nominal or None."""
    best, best_err = None, 0.10
    for rep in TRACK_REPS:
        err = abs(dist_m - rep) / rep
        if err <= best_err:
            best, best_err = rep, err
    return best


# ---------- classification ----------

RACE_NAME_RE = re.compile(r"\brace\b|parkrun|\btt\b|time.trial|\b5k\b|\b10k\b|half.marathon|marathon", re.I)


def _is_race(act, structured, total_dist, avg_hr, lthr):
    if structured:
        return False
    name = (act.get("name") or "") + " " + (act.get("source_file") or "")
    if RACE_NAME_RE.search(name):
        return True
    for rd in RACE_DISTANCES:
        if abs(total_dist - rd) / rd <= 0.015:
            # sustained hard effort: no structured bouts; when HR is known,
            # require race effort so an easy 5 km isn't a race
            return not (avg_hr and lthr) or avg_hr >= 0.9 * lthr
    return False


def classify(act, settings):
    """Full classification. Returns dict with all derived fields.

    settings: lthr (or None), max_hr, easy_cap_hr.
    """
    points = act["points"]
    total_dist = points[-1]["dist"] - points[0]["dist"] if len(points) > 1 else 0
    elapsed = points[-1]["t"] - points[0]["t"] if len(points) > 1 else 0
    hrs = [p["hr"] for p in points if p.get("hr") is not None]
    avg_hr = sum(hrs) / len(hrs) if hrs else None
    max_hr = max(hrs) if hrs else None
    # pace over timer time: watch pauses don't count, standing still does
    timer = act.get("timer_s") or timer_time(points)
    avg_pace = timer / (total_dist / 1000.0) if total_dist > 10 and timer else None  # s/km

    pace = _smoothed_pace(points)
    # Prefer the athlete's laps; fall back to detecting bouts from pace when
    # there are none or they don't show a workout.
    lap_bouts = bouts_from_laps(points, pace, act.get("laps"))
    bouts, track, work, structured = _analyze(points, lap_bouts or [])
    if not structured:
        bouts, track, work, structured = _analyze(
            points, detect_bouts(points, pace))

    result = {
        "distance_m": total_dist, "elapsed_s": elapsed, "timer_s": timer,
        "avg_hr": avg_hr, "max_hr": max_hr, "avg_pace_s_per_km": avg_pace,
        # reps only exist for workouts; pickups in an easy run aren't reps
        "bouts": [], "track_detected": track,
        "classification": "easy", "template": None, "template_label": None,
        "quality_minutes": 0.0, "verdict": None, "hr_drift": None,
        "easy_discipline_pct": None,
    }

    # easy discipline: % of time under easy cap
    cap = settings.get("easy_cap_hr")
    if cap and hrs:
        under = sum(1 for p in points
                    if p.get("hr") is not None and p["hr"] <= cap)
        total = sum(1 for p in points if p.get("hr") is not None)
        result["easy_discipline_pct"] = 100.0 * under / total if total else None

    # 1. race
    if _is_race(act, structured, total_dist, avg_hr, settings.get("lthr")):
        result["classification"] = "race"
        # a race is one continuous effort, so check the whole trace
        result["track_detected"] = _laps_small_area(points, total_dist)
        return result

    # 2. threshold (structural checks)
    if structured:
        result["classification"] = "threshold"
        result["bouts"] = work
        result["quality_minutes"] = sum(b["duration_s"] for b in work) / 60.0
        result["template"], result["template_label"] = _match_template(work, track)
        result["verdict"], result["hr_drift"] = _verdict(work, settings)
        return result

    # 3. long run decided at week level (see analysis.refresh_weeks);
    #    per-activity fallback: >= 90 min and easy intensity
    if elapsed >= 5400 and cap and avg_hr and avg_hr <= cap:
        result["classification"] = "long_run"
        return result

    return result


def _analyze(points, bouts):
    """Track-snap bouts and find the main set. Returns (bouts, track, work,
    structured): work is the longest chain of bouts with short recoveries
    (strides or pickups in the warmup/cooldown fall outside it)."""
    track = detect_track(points, bouts)
    for b in bouts:
        nominal = snap_bout_distance(b["distance_m"]) if track else None
        b["nominal_m"] = nominal
        use_dist = nominal if nominal else b["distance_m"]
        b["pace_s_per_km"] = b["duration_s"] / (use_dist / 1000.0)
    work = _work_set(bouts)
    return bouts, track, work, _is_threshold(work)


def _is_recovery(prev, nxt):
    """Short enough to be a recovery between reps: <= 180 s, or up to 60%
    of the preceding rep for longer reps (e.g. 3 min jog after a mile)."""
    return nxt["start_t"] - prev["end_t"] <= max(180, 0.6 * prev["duration_s"])


def _work_set(bouts):
    """Longest (by work time) run of consecutive bouts separated by
    recoveries (see _is_recovery)."""
    best, cur = [], []
    for b in bouts:
        if cur and not _is_recovery(cur[-1], b):
            cur = []
        cur.append(b)
        if sum(x["duration_s"] for x in cur) > sum(x["duration_s"] for x in best):
            best = list(cur)
    if not best:
        return best
    # Drop warmup/cooldown pickups chained onto either end: bouts much slower
    # than the main reps (time-weighted median pace, so long reps dominate).
    def pace(b):
        return b["duration_s"] / b["distance_m"]
    by_pace = sorted(best, key=pace)
    half, acc = sum(b["duration_s"] for b in best) / 2.0, 0.0
    for b in by_pace:
        acc += b["duration_s"]
        if acc >= half:
            ref = pace(b)
            break
    while best and pace(best[0]) > 1.15 * ref:
        best = best[1:]
    while best and pace(best[-1]) > 1.15 * ref:
        best = best[:-1]
    return best


def _is_threshold(bouts):
    # A short finisher (e.g. 5x1k + 200m) stays in the session and its label
    # but doesn't count as a rep.
    reps = [b for b in bouts if b["distance_m"] >= 350 or b["duration_s"] >= 150]
    if len(reps) < 3:
        return False
    # each rep 400m-2mi OR 150s-12min (spec: 400m-3km or 3-12min; allow 150s
    # for short reps and 2 mi reps, which GPS often measures a bit over 3200m)
    for b in reps:
        ok_dist = 350 <= b["distance_m"] <= 3300
        ok_dur = 150 <= b["duration_s"] <= 750
        if not (ok_dist or ok_dur):
            return False
    # separated by short recoveries
    for x, y in zip(bouts, bouts[1:]):
        if not _is_recovery(x, y):
            return False
    # clearly harder than the surrounding running: reps are well under easy
    # pace, a steady medium-effort run with surges is not
    rel = _median([b.get("rel_pace") for b in reps])
    if rel is not None and rel > 0.80:
        return False
    # total quality >= 10 min
    if sum(b["duration_s"] for b in bouts) < 600:
        return False
    # pace stability across bouts (CV <= 6%); a last rep that's the fastest
    # is a finishing kick, not a sign of an unstructured session
    paces = [b["pace_s_per_km"] for b in reps]
    if len(paces) > 3 and paces[-1] == min(paces):
        paces = paces[:-1]
    mean = sum(paces) / len(paces)
    var = sum((p - mean) ** 2 for p in paces) / len(paces)
    if mean == 0 or math.sqrt(var) / mean > 0.06:
        return False
    return True


MILE_M = 1609.34


def _rep_distance_label(dist_m, track):
    """'1.5 mile' for road reps of a mile or more that land on a quarter
    mile; meters otherwise, and always on the track ('1200m')."""
    if not track:
        miles = dist_m / MILE_M
        quarters = round(miles * 4) / 4.0
        if quarters >= 1 and abs(miles - quarters) / quarters <= 0.05:
            return "%g mile" % quarters
    return "%dm" % int(round(dist_m / 100) * 100)


def _match_template(bouts, track=False):
    """Return (template_key, label), e.g. ('short', '6x1200m') or, for a
    mixed road session, ('medium', '2x1.5 mile + 800m')."""
    rep_m = _median([b.get("nominal_m") or b["distance_m"] for b in bouts])
    rep_s = _median([b["duration_s"] for b in bouts])
    if rep_m < 1500 or rep_s < 300:
        key = "short"
    elif rep_m < 2500 or rep_s < 540:
        key = "medium"
    else:
        key = "long"
    # group consecutive reps of similar length (within 10%)
    groups = []
    for b in bouts:
        d = b.get("nominal_m") or b["distance_m"]
        if groups and abs(d - _median(groups[-1])) / _median(groups[-1]) <= 0.10:
            groups[-1].append(d)
        else:
            groups.append([d])
    parts = []
    for g in groups:
        dist = _rep_distance_label(_median(g), track)
        parts.append(dist if len(g) == 1 else "%dx%s" % (len(g), dist))
    return key, " + ".join(parts)


def _verdict(bouts, settings):
    """HR execution verdict. Returns (verdict, hr_drift)."""
    lthr = settings.get("lthr")
    if not lthr:
        return None, None
    hrs = [b["avg_hr"] for b in bouts if b.get("avg_hr") is not None]
    if len(hrs) < 3:
        return None, None
    third = max(1, len(hrs) // 3)
    drift = sum(hrs[-third:]) / third - sum(hrs[:third]) / third
    late_max = max(hrs[-third:])
    paces = [b["pace_s_per_km"] for b in bouts]
    faded = (paces[-1] - paces[0]) / paces[0] > 0.05 if paces[0] else False
    if late_max > lthr + 6 or faded:
        return "overcooked", drift
    if late_max > lthr + 2 or drift > 5:
        return "drifted_hot", drift
    return "in_zone", drift


# ---------- splits ----------

MILE_M = 1609.344


def _split_row(points, a, b, t0, t1, d0, d1):
    hrs = [points[k]["hr"] for k in range(a, b + 1) if points[k].get("hr") is not None]
    # watch time: drop pauses (recording gaps) inside the split, as timer_time
    paused = sum(dt for dt in (points[k + 1]["t"] - points[k]["t"] for k in range(a, b))
                 if dt > 10)
    dur, dist = t1 - t0 - paused, d1 - d0
    return {"distance_m": dist, "duration_s": dur,
            "pace_s_per_km": 1000.0 * dur / dist if dist > 0 else None,
            "avg_hr": sum(hrs) / len(hrs) if hrs else None,
            "max_hr": max(hrs) if hrs else None}


def splits(act, track):
    """(source, rows): the watch's laps when it recorded more than one,
    else splits computed from the stream every mile (every 400 m on a
    track). Rows: distance_m, duration_s, pace_s_per_km, avg_hr, max_hr."""
    points = [p for p in act["points"] if p.get("dist") is not None]
    if len(points) < 2:
        return {"source": None, "rows": []}
    ts = [p["t"] for p in points]
    laps = [l for l in (act.get("laps") or [])
            if l.get("start_t") is not None and l.get("end_t") is not None]
    if len(laps) > 1:
        rows = []
        for lap in laps:
            a = bisect_left(ts, lap["start_t"])
            b = min(bisect_right(ts, lap["end_t"]), len(points)) - 1
            if b > a:
                rows.append(_split_row(points, a, b, points[a]["t"], points[b]["t"],
                                       points[a]["dist"], points[b]["dist"]))
        return {"source": "watch", "rows": rows}
    step = 400.0 if track else MILE_M
    rows, start = [], 0
    d_start, t_start = points[0]["dist"], points[0]["t"]
    target = d_start + step
    for i in range(1, len(points)):
        p, q = points[i - 1], points[i]
        while q["dist"] >= target > p["dist"]:
            # interpolate the moment the split distance was crossed
            f = (target - p["dist"]) / (q["dist"] - p["dist"])
            t_cross = p["t"] + f * (q["t"] - p["t"])
            rows.append(_split_row(points, start, i, t_start, t_cross, d_start, target))
            start, d_start, t_start, target = i, target, t_cross, target + step
    last = points[-1]
    if last["dist"] - d_start > 0.05 * step:  # closing partial split
        rows.append(_split_row(points, start, len(points) - 1, t_start, last["t"],
                               d_start, last["dist"]))
    return {"source": "computed", "rows": rows}
