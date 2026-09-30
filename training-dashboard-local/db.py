"""SQLite storage for the local training dashboard."""

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ext_id TEXT UNIQUE,
    name TEXT,
    start_time REAL,
    sport TEXT,
    distance_m REAL,
    elapsed_s REAL,
    timer_s REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_pace REAL,
    classification TEXT,
    template TEXT,
    template_label TEXT,
    quality_minutes REAL,
    verdict TEXT,
    hr_drift REAL,
    easy_discipline REAL,
    track_detected INTEGER DEFAULT 0,
    auto_longrun INTEGER DEFAULT 0,
    override_class TEXT,
    source_file TEXT,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS bouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER,
    idx INTEGER,
    duration_s REAL,
    distance_m REAL,
    nominal_m REAL,
    pace_s_per_km REAL,
    avg_hr REAL,
    max_hr REAL,
    FOREIGN KEY(activity_id) REFERENCES activities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS weeks (
    week TEXT PRIMARY KEY,
    total_min REAL,
    quality_min REAL,
    share_pct REAL,
    q_sessions INTEGER,
    easy_min REAL,
    km REAL,
    long_run_id INTEGER
);
"""

DEFAULT_SETTINGS = {
    "lthr": "",
    "max_hr": "190",
    "easy_cap_hr": "",          # empty = auto 70% of max HR
    # Bakken Golden-Zone duration factors: {upper_bound_seconds: factor}
    "bakken_factors": json.dumps({"60": 1.00, "180": 1.02, "300": 1.045, "720": 1.075}),
    # race -> LT multipliers (INCREMENT3 conversions)
    "race_lt_factors": json.dumps({"5000": 1.04, "10000": 1.00,
                                   "21097.5": 0.952381, "42195": 0.909091}),
    # Strava OAuth: the user's own API app + tokens from Connect Strava.
    # Never sent to the browser (see get_settings); read with strava_auth().
    "strava_client_id": "",
    "strava_client_secret": "",
    "strava_access_token": "",
    "strava_refresh_token": "",
    "strava_expires_at": "",
    "strava_athlete": "",
}


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # migrations for databases created before a column existed
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(bouts)")}
    if "max_hr" not in cols:
        conn.execute("ALTER TABLE bouts ADD COLUMN max_hr REAL")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(activities)")}
    if "timer_s" not in cols:
        conn.execute("ALTER TABLE activities ADD COLUMN timer_s REAL")
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
    conn.commit()
    return conn


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    s = {r["key"]: r["value"] for r in rows}
    # resolved numerics
    try:
        max_hr = int(s.get("max_hr") or 190)
    except ValueError:
        max_hr = 190
    try:
        lthr = int(s["lthr"]) if s.get("lthr") else None
    except ValueError:
        lthr = None
    try:
        easy_cap = int(s["easy_cap_hr"]) if s.get("easy_cap_hr") else None
    except ValueError:
        easy_cap = None
    if easy_cap is None:
        easy_cap = round(max_hr * 0.70)
    return {
        "lthr": lthr, "max_hr": max_hr, "easy_cap_hr": easy_cap,
        "lthr_raw": s.get("lthr") or "", "max_hr_raw": s.get("max_hr") or "190",
        "easy_cap_raw": s.get("easy_cap_hr") or "",
        "bakken_factors": json.loads(s.get("bakken_factors") or "{}"),
        "race_lt_factors": json.loads(s.get("race_lt_factors") or "{}"),
        "strava_client_id": s.get("strava_client_id") or "",
        "strava_has_secret": bool(s.get("strava_client_secret")),
        "strava_connected": bool(s.get("strava_refresh_token")),
        "strava_athlete": s.get("strava_athlete") or "",
        "easy_cap_auto": not bool(s.get("easy_cap_hr")),
    }


def strava_auth(conn):
    """Strava credentials and tokens (server-side only)."""
    keys = ("strava_client_id", "strava_client_secret", "strava_access_token",
            "strava_refresh_token", "strava_expires_at")
    rows = conn.execute("SELECT key, value FROM settings WHERE key IN (%s)"
                        % ",".join("?" * len(keys)), keys).fetchall()
    s = {r["key"]: r["value"] or "" for r in rows}
    return {k[len("strava_"):]: s.get(k, "") for k in keys}


def save_settings(conn, updates):
    for k, v in updates.items():
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()


def insert_activity(conn, act, cls):
    """Insert a classified activity. Returns id or None if duplicate."""
    import hashlib
    start = act.get("start_time") or 0
    key = "%d|%d|%d" % (round(start / 60),
                        round((act.get("elapsed_s") or cls.get("elapsed_s") or 0)),
                        round(cls.get("distance_m") or 0))
    ext_id = hashlib.md5(key.encode()).hexdigest()[:16]
    cur = conn.execute("SELECT id FROM activities WHERE ext_id=?", (ext_id,))
    if cur.fetchone():
        return None
    cur = conn.execute(
        """INSERT INTO activities(ext_id, name, start_time, sport, distance_m,
           elapsed_s, timer_s, avg_hr, max_hr, avg_pace, classification, template,
           template_label, quality_minutes, verdict, hr_drift, easy_discipline,
           track_detected, source_file, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ext_id, act.get("name"), act.get("start_time"), act.get("sport"),
         cls.get("distance_m"), cls.get("elapsed_s"), cls.get("timer_s"),
         cls.get("avg_hr"),
         cls.get("max_hr"), cls.get("avg_pace_s_per_km"),
         cls.get("classification"), cls.get("template"), cls.get("template_label"),
         cls.get("quality_minutes"), cls.get("verdict"), cls.get("hr_drift"),
         cls.get("easy_discipline_pct"), 1 if cls.get("track_detected") else 0,
         act.get("source_file"), time.time()))
    aid = cur.lastrowid
    for i, b in enumerate(cls.get("bouts") or []):
        conn.execute(
            """INSERT INTO bouts(activity_id, idx, duration_s, distance_m,
               nominal_m, pace_s_per_km, avg_hr, max_hr)
               VALUES(?,?,?,?,?,?,?,?)""",
            (aid, i, b["duration_s"], b["distance_m"], b.get("nominal_m"),
             b["pace_s_per_km"], b.get("avg_hr"), b.get("max_hr")))
    conn.commit()
    return aid


def update_classification(conn, activity_id, classification, cls,
                          clear_override=True):
    # Clears auto_longrun: otherwise refresh_weeks, which resets auto long
    # runs to 'easy', would overwrite the new classification.
    if clear_override:
        conn.execute("UPDATE activities SET override_class=NULL WHERE id=?",
                     (activity_id,))
    conn.execute(
        """UPDATE activities SET classification=?, template=?, template_label=?,
           quality_minutes=?, verdict=?, hr_drift=?, easy_discipline=?,
           track_detected=?, auto_longrun=0, timer_s=?, avg_pace=?
           WHERE id=?""",
        (classification, cls.get("template"), cls.get("template_label"),
         cls.get("quality_minutes"), cls.get("verdict"), cls.get("hr_drift"),
         cls.get("easy_discipline_pct"),
         1 if cls.get("track_detected") else 0,
         cls.get("timer_s"), cls.get("avg_pace_s_per_km"), activity_id))
    conn.execute("DELETE FROM bouts WHERE activity_id=?", (activity_id,))
    for i, b in enumerate(cls.get("bouts") or []):
        conn.execute(
            """INSERT INTO bouts(activity_id, idx, duration_s, distance_m,
               nominal_m, pace_s_per_km, avg_hr, max_hr)
               VALUES(?,?,?,?,?,?,?,?)""",
            (activity_id, i, b["duration_s"], b["distance_m"], b.get("nominal_m"),
             b["pace_s_per_km"], b.get("avg_hr"), b.get("max_hr")))
    conn.commit()
