"""Analysis engine: week aggregates, progression series, LT/race engine.

Pure functions over the DB. All pace values are seconds/km; times are unix.
"""

import datetime
import json

from db import get_settings


def iso_week(ts):
    d = datetime.datetime.fromtimestamp(ts)
    y, w, _ = d.isocalendar()
    return "%04d-W%02d" % (y, w)


def week_start_ts(week_str):
    y, w = week_str.split("-W")
    d = datetime.date.fromisocalendar(int(y), int(w), 1)
    return datetime.datetime(d.year, d.month, d.day).timestamp()


# ---------- weeks ----------

def _run_time(a):
    """Watch timer time (pauses excluded); elapsed for rows stored before
    timer time existed."""
    return a["timer_s"] or a["elapsed_s"] or 0


def refresh_weeks(conn):
    """Recompute weekly aggregates + auto long-run designation (idempotent)."""
    conn.execute("UPDATE activities SET classification='easy', auto_longrun=0 "
                 "WHERE auto_longrun=1")
    acts = conn.execute(
        "SELECT * FROM activities WHERE sport='run' ORDER BY start_time").fetchall()
    by_week = {}
    for a in acts:
        if not a["start_time"]:
            continue
        by_week.setdefault(iso_week(a["start_time"]), []).append(a)
    # auto long run: longest easy run >= 75 min per week
    for wk, lst in by_week.items():
        cands = [a for a in lst if a["classification"] == "easy"
                 and (a["elapsed_s"] or 0) >= 4500]
        if cands:
            best = max(cands, key=lambda a: a["elapsed_s"])
            conn.execute("UPDATE activities SET classification='long_run', "
                         "auto_longrun=1 WHERE id=?", (best["id"],))
    conn.execute("DELETE FROM weeks")
    for wk in sorted(by_week):
        lst = conn.execute(
            "SELECT * FROM activities WHERE sport='run'").fetchall()
        lst = [a for a in lst if a["start_time"] and iso_week(a["start_time"]) == wk]
        total_min = sum(_run_time(a) for a in lst) / 60.0
        # threshold only: a run relabeled by hand (speed, medium-hard, ...)
        # keeps its detected rep minutes but isn't threshold work
        quality_min = sum((a["quality_minutes"] or 0) for a in lst
                          if a["classification"] == "threshold")
        q_sessions = sum(1 for a in lst if a["classification"] == "threshold")
        easy_min = sum(_run_time(a) / 60.0 for a in lst
                       if a["classification"] in ("easy", "long_run"))
        km = sum((a["distance_m"] or 0) for a in lst) / 1000.0
        lr = [a["id"] for a in lst if a["classification"] == "long_run"]
        share = 100.0 * quality_min / total_min if total_min > 0 else 0.0
        conn.execute(
            "INSERT INTO weeks(week, total_min, quality_min, share_pct, q_sessions,"
            " easy_min, km, long_run_id) VALUES(?,?,?,?,?,?,?,?)",
            (wk, total_min, quality_min, share, q_sessions, easy_min, km,
             lr[0] if lr else None))
    conn.commit()


# ---------- progression ----------

def progression(conn):
    """Series for the Progression view."""
    th = conn.execute(
        "SELECT * FROM activities WHERE classification='threshold' "
        "ORDER BY start_time").fetchall()
    by_template = {}
    for a in th:
        bouts = conn.execute(
            "SELECT * FROM bouts WHERE activity_id=? ORDER BY idx", (a["id"],)).fetchall()
        if not bouts:
            continue
        avg_pace = sum(b["pace_s_per_km"] for b in bouts) / len(bouts)
        hrs = [b["avg_hr"] for b in bouts if b["avg_hr"] is not None]
        avg_hr = sum(hrs) / len(hrs) if hrs else None
        med_dur = sorted(b["duration_s"] for b in bouts)[len(bouts) // 2]
        med_dist = sorted(b["nominal_m"] or b["distance_m"] or 0
                          for b in bouts)[len(bouts) // 2]
        key = a["template"] or "short"
        by_template.setdefault(key, []).append({
            "activity_id": a["id"], "date": a["start_time"],
            "label": a["template_label"], "avg_rep_pace": avg_pace,
            "avg_rep_hr": avg_hr, "quality_min": a["quality_minutes"],
            "med_rep_s": med_dur, "med_rep_m": med_dist,
            "track": bool(a["track_detected"]),
        })
    weeks = [dict(r) for r in conn.execute("SELECT * FROM weeks ORDER BY week")]
    races = [dict(r) for r in conn.execute(
        "SELECT id, name, start_time, distance_m, elapsed_s, avg_hr, track_detected "
        "FROM activities "
        "WHERE classification='race' ORDER BY start_time")]
    return {"by_template": by_template, "weeks": weeks, "races": races}


# ---------- LT engine (Increment 3) ----------

def bakken_factor(rep_s, factors):
    """Interpolate Bakken duration factor. factors: {upper_bound_s: f}."""
    pts = sorted((float(k), float(v)) for k, v in factors.items())
    if not pts:
        return 1.0
    if rep_s <= pts[0][0]:
        return pts[0][1]
    for (s0, f0), (s1, f1) in zip(pts, pts[1:]):
        if rep_s <= s1:
            t = (rep_s - s0) / (s1 - s0)
            return f0 + t * (f1 - f0)
    return pts[-1][1]


def workout_lt_points(conn, settings):
    """Per threshold session: estimated LT pace via Bakken duration factors."""
    factors = settings["bakken_factors"]
    prog = progression(conn)
    pts = []
    for key, series in prog["by_template"].items():
        for s in series:
            f = bakken_factor(s["med_rep_s"], factors)
            pts.append({
                "activity_id": s["activity_id"], "date": s["date"],
                "label": s["label"], "lt_pace": s["avg_rep_pace"] / f,
                "rep_pace": s["avg_rep_pace"], "factor": f,
                "avg_rep_hr": s["avg_rep_hr"],
            })
    pts.sort(key=lambda p: p["date"])
    return pts


def _race_factor(rd, factors):
    """Look up the LT multiplier for a race distance, tolerating key types."""
    for k in (str(int(rd)), str(rd), str(float(rd))):
        if k in factors:
            return float(factors[k])
    raise KeyError(rd)


def _nearest_race_distance(dist_m, factors):
    best, best_err = None, 0.03
    for rd in factors:
        err = abs(dist_m - float(rd)) / float(rd)
        if err <= best_err:
            best, best_err = float(rd), err
    return best


def race_predictions(conn, settings):
    """Race-derived predicted LT pace step line + HR effort qualifier."""
    factors = settings["race_lt_factors"]
    lthr, max_hr = settings["lthr"], settings["max_hr"]
    races = conn.execute(
        "SELECT * FROM activities WHERE classification='race' "
        "ORDER BY start_time").fetchall()
    out = []
    for r in races:
        if not r["distance_m"] or not r["elapsed_s"]:
            continue
        rd = _nearest_race_distance(r["distance_m"], factors)
        race_pace = r["elapsed_s"] / (r["distance_m"] / 1000.0)
        if rd is None:
            out.append({"activity_id": r["id"], "name": r["name"],
                        "date": r["start_time"], "distance_m": r["distance_m"],
                        "track": bool(r["track_detected"]),
                        "time_s": r["elapsed_s"], "race_pace": race_pace,
                        "predicted_lt": None, "note": "non-standard distance",
                        "qualifier": None, "avg_hr": r["avg_hr"]})
            continue
        mult = _race_factor(rd, factors)
        pred_lt = race_pace * mult
        qualifier = None
        avg_hr = r["avg_hr"]
        if avg_hr is None:
            qualifier = "no-hr"
        elif lthr and rd <= 10000 and avg_hr < lthr - 5:
            qualifier = "partial"
        out.append({"activity_id": r["id"], "name": r["name"],
                    "date": r["start_time"], "distance_m": r["distance_m"],
                    "track": bool(r["track_detected"]),
                    "time_s": r["elapsed_s"], "race_pace": race_pace,
                    "predicted_lt": pred_lt, "race_dist": rd,
                    "factor": mult, "qualifier": qualifier, "avg_hr": avg_hr})
    return out


def race_execution(conn, settings):
    """Per race: actual vs predicted-from-training time."""
    factors = settings["race_lt_factors"]
    lt_pts = workout_lt_points(conn, settings)
    results = []
    for rp in race_predictions(conn, settings):
        if rp["predicted_lt"] is None or rp["race_dist"] is None:
            results.append({**rp, "predicted_time": None, "delta_s": None,
                            "delta_pct": None})
            continue
        rd = rp["race_dist"]
        # mean workout-derived LT over 6 weeks before the race
        cutoff = rp["date"] - 6 * 7 * 86400
        prior = [p["lt_pace"] for p in lt_pts if cutoff <= p["date"] < rp["date"]]
        if not prior:
            results.append({**rp, "predicted_time": None, "delta_s": None,
                            "delta_pct": None,
                            "note": "no threshold sessions in prior 6 weeks"})
            continue
        mean_lt = sum(prior) / len(prior)
        mult = _race_factor(rd, factors)
        # invert: race_pace = LT / mult
        pred_race_pace = mean_lt / mult
        pred_time = pred_race_pace * (rp["distance_m"] / 1000.0)
        delta = rp["time_s"] - pred_time
        results.append({**rp, "predicted_time": pred_time, "delta_s": delta,
                        "delta_pct": 100.0 * delta / pred_time if pred_time else None,
                        "n_prior": len(prior)})
    return results


def advisory(conn, settings):
    """Read-only suggested LT pace going forward (latest race)."""
    preds = [p for p in race_predictions(conn, settings)
             if p["predicted_lt"] is not None]
    if not preds:
        return None
    latest = max(preds, key=lambda p: p["date"])
    return {"lt_pace": latest["predicted_lt"], "from_race": latest["name"],
            "date": latest["date"], "qualifier": latest["qualifier"]}
