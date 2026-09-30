"""File parsers: FIT / GPX / TCX / ZIP -> normalized activity dict.

Normalized activity:
  {
    "name": str, "start_time": unix_ts, "sport": "run"|"other",
    "points": [{"t": ts, "lat": .., "lon": .., "alt": .., "hr": .., "dist": meters}],
    "laps": [{"start_t":.., "end_t":.., "elapsed": s, "distance": m,
              "avg_hr":..}]  # may be []
    "source_file": filename,
  }
Points are sorted by time; dist is cumulative meters from the device when
present, else computed from GPS via haversine.
"""

import math
import os
import zipfile
import xml.etree.ElementTree as ET

import fitparse_min

SPORT_NAMES = {0: "other", 1: "run", 2: "other", 3: "other", 4: "other",
               5: "other", 6: "other"}


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _fill_dist_from_gps(points):
    """Compute cumulative distance when the device didn't provide it."""
    dist = 0.0
    prev = None
    for p in points:
        if p.get("dist") is None:
            if prev and p.get("lat") is not None and prev.get("lat") is not None:
                dist += _haversine_m(prev["lat"], prev["lon"], p["lat"], p["lon"])
            p["dist"] = dist
        else:
            dist = p["dist"]
        prev = p
    return points


def _dedup_sort(points):
    points = [p for p in points if p.get("t") is not None]
    points.sort(key=lambda p: p["t"])
    out = []
    for p in points:
        if out and p["t"] <= out[-1]["t"]:
            continue
        out.append(p)
    return out


def parse_fit(data, filename):
    parsed = fitparse_min.parse(data)
    points = []
    for r in parsed["records"]:
        points.append({"t": r["t"], "lat": r["lat"], "lon": r["lon"],
                       "alt": r["alt"], "hr": r["hr"], "dist": r["dist"]})
    points = _dedup_sort(points)
    _fill_dist_from_gps(points)
    sess = parsed["session"]
    sport_num = sess.get("sport")
    start = points[0]["t"] if points else parsed["file_id_time"]
    return {
        "name": os.path.splitext(os.path.basename(filename))[0],
        "start_time": start,
        "sport": "run" if sport_num in (None, 1) else "other",
        "points": points,
        "laps": parsed["laps"],
        "timer_s": sess.get("timer"),
        "source_file": os.path.basename(filename),
    }


def _gpx_time(s):
    # 2026-09-28T18:02:00Z or with offset
    s = s.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def parse_gpx(data, filename):
    root = ET.fromstring(data)
    ns = {"g": "http://www.topografix.com/GPX/1/1",
          "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"}
    points = []
    for trkpt in root.findall(".//g:trkpt", ns) or root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt"):
        try:
            lat = float(trkpt.get("lat"))
            lon = float(trkpt.get("lon"))
        except (TypeError, ValueError):
            continue
        p = {"t": None, "lat": lat, "lon": lon, "alt": None, "hr": None, "dist": None}
        ele = trkpt.find("g:ele", ns)
        if ele is not None and ele.text:
            try:
                p["alt"] = float(ele.text)
            except ValueError:
                pass
        t = trkpt.find("g:time", ns)
        if t is not None and t.text:
            p["t"] = _gpx_time(t.text)
        hr = trkpt.find(".//tpx:hr", ns)
        if hr is not None and hr.text:
            try:
                p["hr"] = int(float(hr.text))
            except ValueError:
                pass
        points.append(p)
    points = _dedup_sort(points)
    _fill_dist_from_gps(points)
    start = points[0]["t"] if points else None
    name_el = root.find(".//g:trk/g:name", ns)
    name = (name_el.text if name_el is not None and name_el.text else
            os.path.splitext(os.path.basename(filename))[0])
    return {"name": name, "start_time": start, "sport": "run", "points": points,
            "laps": [], "source_file": os.path.basename(filename)}


def parse_tcx(data, filename):
    root = ET.fromstring(data)
    ns = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    points = []
    laps = []
    for lap in root.findall(".//t:Lap", ns):
        ld = {}
        try:
            ld["elapsed"] = float(lap.find("t:TotalTimeSeconds", ns).text)
        except (AttributeError, ValueError, TypeError):
            ld["elapsed"] = None
        ld["start_t"] = _gpx_time(lap.get("StartTime")) if lap.get("StartTime") else None
        ld["end_t"] = (ld["start_t"] + ld["elapsed"]
                       if ld["start_t"] is not None and ld["elapsed"] is not None
                       else None)
        try:
            ld["distance"] = float(lap.find("t:DistanceMeters", ns).text)
        except (AttributeError, ValueError, TypeError):
            ld["distance"] = None
        try:
            ld["avg_hr"] = int(lap.find("t:AverageHeartRateBpm/t:Value", ns).text)
        except (AttributeError, ValueError, TypeError):
            ld["avg_hr"] = None
        laps.append(ld)
    for tp in root.findall(".//t:Trackpoint", ns):
        p = {"t": None, "lat": None, "lon": None, "alt": None, "hr": None, "dist": None}
        t = tp.find("t:Time", ns)
        if t is not None and t.text:
            p["t"] = _gpx_time(t.text)
        lat = tp.find("t:Position/t:LatitudeDegrees", ns)
        lon = tp.find("t:Position/t:LongitudeDegrees", ns)
        if lat is not None and lat.text and lon is not None and lon.text:
            try:
                p["lat"] = float(lat.text)
                p["lon"] = float(lon.text)
            except ValueError:
                pass
        alt = tp.find("t:AltitudeMeters", ns)
        if alt is not None and alt.text:
            try:
                p["alt"] = float(alt.text)
            except ValueError:
                pass
        d = tp.find("t:DistanceMeters", ns)
        if d is not None and d.text:
            try:
                p["dist"] = float(d.text)
            except ValueError:
                pass
        hr = tp.find("t:HeartRateBpm/t:Value", ns)
        if hr is not None and hr.text:
            try:
                p["hr"] = int(float(hr.text))
            except ValueError:
                pass
        points.append(p)
    points = _dedup_sort(points)
    _fill_dist_from_gps(points)
    start = points[0]["t"] if points else None
    return {"name": os.path.splitext(os.path.basename(filename))[0],
            "start_time": start, "sport": "run", "points": points,
            "laps": laps, "source_file": os.path.basename(filename)}


def parse_file(filename, data):
    """Parse a single file by extension. Returns normalized activity or None."""
    low = filename.lower()
    if low.endswith(".fit"):
        return parse_fit(data, filename)
    if low.endswith(".gpx"):
        return parse_gpx(data, filename)
    if low.endswith((".tcx", ".xml")):
        return parse_tcx(data, filename)
    return None


def parse_upload(filename, data):
    """Handle a single file or a .zip of files.

    Returns list of {"act": activity, "filename": name, "data": raw bytes}."""
    if filename.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                inner = info.filename
                if not inner.lower().endswith((".fit", ".gpx", ".tcx", ".xml")):
                    continue
                try:
                    raw = z.read(info)
                    act = parse_file(inner, raw)
                except Exception:
                    continue
                if act and len(act["points"]) > 30:
                    out.append({"act": act, "filename": inner, "data": raw})
        return out
    act = parse_file(filename, data)
    if act is None or len(act["points"]) <= 30:
        return []
    return [{"act": act, "filename": filename, "data": data}]
