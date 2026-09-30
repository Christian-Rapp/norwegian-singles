"""Minimal Strava import (stdlib only): activities + streams via access token.

Used by the local app's Import view. The user pastes a short-lived access
token (obtained via their own OAuth code exchange); nothing is stored except
what the user explicitly saves in Settings.
"""

import json
import urllib.request
import urllib.error

API = "https://www.strava.com/api/v3"


class RateLimited(RuntimeError):
    pass


def _get(path, token, params=""):
    url = API + path + params
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        if e.code == 429:
            raise RateLimited("Strava rate limit reached")
        raise RuntimeError("Strava %s: %s" % (e.code, body))


def fetch_activities(token, after_ts):
    """All athlete activities since after_ts (unix). Paginates."""
    out, page = [], 1
    while True:
        batch = _get("/athlete/activities",
                     token, "?after=%d&per_page=200&page=%d" % (after_ts, page))
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 200:
            break
        page += 1
    return out


def fetch_streams(token, activity_id):
    keys = "time,distance,latlng,altitude,heartrate"
    data = _get("/activities/%s/streams" % activity_id, token,
                "?keys=%s&key_by_type=true" % keys)
    # key_by_type=true returns {"time": {"data": [...]}, ...}, not a list
    if isinstance(data, dict):
        data = [dict(v, type=k) for k, v in data.items() if isinstance(v, dict)]
    return {s["type"]: s["data"] for s in data if s.get("data")}


def fetch_laps(token, activity_id):
    """Device laps (manual or auto). A run with no laps comes back as a
    single lap covering the whole activity."""
    return _get("/activities/%s/laps" % activity_id, token)


def streams_to_points(summary, streams):
    """Convert Strava summary + streams to normalized points."""
    import datetime
    t0 = datetime.datetime.fromisoformat(
        summary["start_date"].replace("Z", "+00:00")).timestamp()
    time_s = streams.get("time") or []
    dist = streams.get("distance") or []
    latlng = streams.get("latlng") or []
    alt = streams.get("altitude") or []
    hr = streams.get("heartrate") or []
    n = len(time_s)
    points = []
    for i in range(n):
        p = {"t": t0 + time_s[i], "lat": None, "lon": None, "alt": None,
             "hr": None, "dist": None}
        if i < len(dist):
            p["dist"] = dist[i]
        if i < len(latlng) and latlng[i]:
            p["lat"], p["lon"] = latlng[i][0], latlng[i][1]
        if i < len(alt):
            p["alt"] = alt[i]
        if i < len(hr):
            p["hr"] = hr[i]
        points.append(p)
    return points


RUN_TYPES = ("run", "trailrun", "virtualrun")


def is_run(summary):
    return (summary.get("sport_type") or summary.get("type") or "").lower() in RUN_TYPES


def laps_to_ranges(laps, points):
    """Strava laps -> normalized laps with start_t/end_t. start_index and
    end_index index the streams, which align 1:1 with points."""
    out = []
    for lap in laps or []:
        a, b = lap.get("start_index"), lap.get("end_index")
        if a is None or b is None or not points:
            continue
        a, b = min(a, len(points) - 1), min(b, len(points) - 1)
        out.append({"start_t": points[a]["t"], "end_t": points[b]["t"],
                    "distance": lap.get("distance"),
                    "avg_hr": lap.get("average_heartrate")})
    return out


def to_activity(summary, points, laps=None):
    sport = (summary.get("sport_type") or summary.get("type") or "").lower()
    return {
        "name": summary.get("name") or "Strava activity",
        "start_time": points[0]["t"] if points else None,
        "sport": "run" if sport in RUN_TYPES else "other",
        "points": points,
        "laps": laps_to_ranges(laps, points),
        "source_file": "strava:%s" % summary.get("id"),
        "strava_id": summary.get("id"),
    }
