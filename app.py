"""Local training dashboard server (stdlib only).

Run:  python3 app.py [port]
Then open http://localhost:8000 in a browser.

Data lives in ./data/dashboard.db and ./data/uploads/ next to this file.
"""

import datetime
import io
import json
import os
import sys
import traceback
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import analysis
import classifier
import db
import parsers
import strava

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
UPLOADS = os.path.join(DATA, "uploads")
STATIC = os.path.join(BASE, "static")

MIME = {".html": "text/html", ".js": "application/javascript",
        ".css": "text/css", ".json": "application/json",
        ".png": "image/png", ".ico": "image/x-icon"}


def _stored_path(aid):
    for ext in (".fit", ".gpx", ".tcx", ".xml", ".json"):
        p = os.path.join(UPLOADS, "%d%s" % (aid, ext))
        if os.path.isfile(p):
            return p
    return None


def _load_stored(aid, source_file):
    """Normalized activity (points, laps) from its stored upload, or None."""
    stored = _stored_path(aid)
    if not stored:
        return None
    with open(stored, "rb") as f:
        data = f.read()
    if stored.endswith(".json"):
        raw = json.loads(data)
        points = strava.streams_to_points(raw["summary"], raw["streams"])
        return strava.to_activity(raw["summary"], points, raw.get("laps"))
    items = parsers.parse_upload(source_file or "file.fit", data)
    return items[0]["act"] if items else None


def _settings_for_classifier(s):
    return {"lthr": s["lthr"], "max_hr": s["max_hr"],
            "easy_cap_hr": s["easy_cap_hr"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "SinglesDashboard/1.0"

    # ----- helpers -----
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _html_message(self, code, title, text):
        esc = lambda v: (v.replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;"))
        self._send(code, "<!doctype html><meta charset=utf-8><title>%s</title>"
                   "<body style='font-family:Verdana,sans-serif;padding:30px'>"
                   "<h2>%s</h2><p>%s</p><p><a href='/#settings'>Back to Settings</a></p>"
                   % (esc(title), esc(title), esc(text)), "text/html; charset=utf-8")

    def _json_body(self):
        ln = int(self.headers.get("Content-Length", 0))
        if not ln:
            return {}
        return json.loads(self.rfile.read(ln).decode() or "{}")

    def _db(self):
        return db.connect(os.path.join(DATA, "dashboard.db"))

    # ----- multipart (minimal) -----
    def _parse_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return []
        boundary = ctype.split("boundary=")[1].encode()
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln)
        parts = []
        for chunk in raw.split(b"--" + boundary):
            if b"\r\n\r\n" not in chunk:
                continue
            head, body = chunk.split(b"\r\n\r\n", 1)
            if body.endswith(b"\r\n"):
                body = body[:-2]
            head_s = head.decode("utf-8", "replace")
            if 'filename="' not in head_s:
                continue
            fname = head_s.split('filename="')[1].split('"')[0]
            fname = os.path.basename(fname)
            if fname:
                parts.append((fname, body))
        return parts

    # ----- routes -----
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/" or path == "/index.html":
            return self._serve_static("index.html")
        if path == "/strava/connect":
            return self._strava_connect()
        if path == "/strava/callback":
            return self._strava_callback(parse_qs(url.query))
        if path.startswith("/api/"):
            return self._api_get(path)
        if path in ("/app.js", "/style.css", "/favicon.ico"):
            return self._serve_static(path.lstrip("/"))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/upload":
            return self._api_upload()
        if path == "/api/settings":
            return self._api_save_settings()
        if path == "/api/reclassify":
            return self._api_reclassify()
        if path == "/api/strava/import":
            return self._api_strava_import()
        if path == "/api/strava/credentials":
            return self._api_strava_credentials()
        if path == "/api/strava/disconnect":
            return self._api_strava_disconnect()
        self._send(404, {"error": "not found"})

    def _serve_static(self, name):
        fpath = os.path.join(STATIC, name)
        if not os.path.isfile(fpath):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(name)[1]
        with open(fpath, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    # ----- API -----
    def _api_get(self, path):
        conn = self._db()
        try:
            if path == "/api/overview":
                weeks = [dict(r) for r in
                         conn.execute("SELECT * FROM weeks ORDER BY week DESC")]
                return self._send(200, {"weeks": weeks})
            if path.startswith("/api/week/"):
                wk = path.rsplit("/", 1)[1]
                try:
                    t0 = analysis.week_start_ts(wk)
                except ValueError:
                    return self._send(400, {"error": "bad week"})
                # calendar days, not 7*86400 s, so a DST change can't shift it
                t1 = (datetime.datetime.fromtimestamp(t0)
                      + datetime.timedelta(days=7)).timestamp()
                runs = [dict(r) for r in conn.execute(
                    """SELECT id, name, start_time, distance_m, elapsed_s, timer_s, avg_hr,
                              avg_pace, classification, template_label, quality_minutes,
                              verdict, track_detected
                       FROM activities WHERE start_time >= ? AND start_time < ?
                       ORDER BY start_time""", (t0, t1))]
                return self._send(200, {"week": wk, "runs": runs})
            if path.startswith("/api/activity/"):
                aid = int(path.rsplit("/", 1)[1])
                a = conn.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
                if not a:
                    return self._send(404, {"error": "not found"})
                bouts = [dict(r) for r in conn.execute(
                    "SELECT * FROM bouts WHERE activity_id=? ORDER BY idx", (aid,))]
                body = {"activity": dict(a), "bouts": bouts}
                act = _load_stored(aid, a["source_file"])
                if act:
                    # 400 m splits only for track races; a track workout's
                    # warmup/cooldown would make that dozens of rows
                    body["splits"] = classifier.splits(
                        act, a["classification"] == "race" and bool(a["track_detected"]))
                return self._send(200, body)
            if path == "/api/progression":
                body = analysis.progression(conn)
                body["vdot"] = analysis.vdot_series(conn, db.get_settings(conn))
                return self._send(200, body)
            if path == "/api/lt":
                s = db.get_settings(conn)
                return self._send(200, {
                    "workout_lt": analysis.workout_lt_points(conn, s),
                    "race_predictions": analysis.race_predictions(conn, s),
                    "race_execution": analysis.race_execution(conn, s),
                    "advisory": analysis.advisory(conn, s),
                    "bakken_factors": s["bakken_factors"],
                    "race_lt_factors": s["race_lt_factors"],
                })
            if path == "/api/settings":
                return self._send(200, db.get_settings(conn))
            return self._send(404, {"error": "not found"})
        finally:
            conn.close()

    def _classify_and_store(self, conn, act, filename, raw_bytes):
        s = db.get_settings(conn)
        cls = classifier.classify(act, _settings_for_classifier(s))
        aid = db.insert_activity(conn, act, cls)
        if aid and raw_bytes:
            ext = os.path.splitext(filename)[1].lower() or ".bin"
            with open(os.path.join(UPLOADS, "%d%s" % (aid, ext)), "wb") as f:
                f.write(raw_bytes)
        return aid

    def _api_upload(self):
        conn = self._db()
        try:
            os.makedirs(UPLOADS, exist_ok=True)
            imported, skipped = 0, 0
            for fname, data in self._parse_multipart():
                try:
                    items = parsers.parse_upload(fname, data)
                except Exception:
                    skipped += 1
                    continue
                for item in items:
                    aid = self._classify_and_store(conn, item["act"],
                                                   item["filename"], item["data"])
                    if aid:
                        imported += 1
                    else:
                        skipped += 1
            analysis.refresh_weeks(conn)
            return self._send(200, {"imported": imported, "skipped": skipped})
        finally:
            conn.close()

    def _api_save_settings(self):
        conn = self._db()
        try:
            body = self._json_body()
            updates = {}
            for k in ("lthr", "max_hr", "easy_cap_hr"):
                if k in body:
                    updates[k] = str(body[k] or "")
            for k in ("bakken_factors", "race_lt_factors"):
                if k in body:
                    updates[k] = json.dumps(body[k])
            if updates:
                db.save_settings(conn, updates)
                self._reclassify_all(conn)
            return self._send(200, {"ok": True, "settings": db.get_settings(conn)})
        finally:
            conn.close()

    @staticmethod
    def _reclassify_all(conn):
        """Re-run the classifier over stored upload files (spec: Data -> re-run)."""
        s = db.get_settings(conn)
        for a in conn.execute("SELECT id, source_file, override_class FROM activities"):
            aid = a["id"]
            stored = _stored_path(aid)
            if not stored:
                continue
            try:
                act = _load_stored(aid, a["source_file"])
                if not act:
                    continue
                cls = classifier.classify(act, _settings_for_classifier(s))
                eff = a["override_class"] or cls["classification"]
                db.update_classification(conn, aid, eff, cls,
                                         clear_override=False)
            except Exception:
                sys.stderr.write("Reclassify failed for activity %d (%s):\n"
                                 % (aid, stored))
                traceback.print_exc()
                continue
        analysis.refresh_weeks(conn)

    def _api_reclassify(self):
        conn = self._db()
        try:
            body = self._json_body()
            aid = int(body["id"])
            new_cls = body["classification"]
            a = conn.execute("SELECT * FROM activities WHERE id=?", (aid,)).fetchone()
            if not a:
                return self._send(404, {"error": "not found"})
            conn.execute("UPDATE activities SET override_class=?, classification=?, "
                         "auto_longrun=0 WHERE id=?", (new_cls, new_cls, aid))
            conn.commit()
            analysis.refresh_weeks(conn)
            return self._send(200, {"ok": True})
        finally:
            conn.close()

    def _backfill_laps(self, token, aid, strava_id):
        """Add Strava laps to a stored activity imported before laps were
        fetched. Returns True when laps were added."""
        path = os.path.join(UPLOADS, "%d.json" % aid)
        if not os.path.isfile(path):
            return False
        with open(path) as f:
            raw = json.load(f)
        if "laps" in raw:
            return False
        raw["laps"] = strava.fetch_laps(token, strava_id)
        with open(path, "w") as f:
            json.dump(raw, f)
        return True

    # ----- Strava connect (OAuth) -----
    def _strava_connect(self):
        conn = self._db()
        try:
            auth = db.strava_auth(conn)
        finally:
            conn.close()
        if not auth["client_id"] or not auth["client_secret"]:
            return self._html_message(400, "Strava app not set up",
                                      "Enter your Strava Client ID and Client "
                                      "Secret in Settings first.")
        # Strava only redirects to the app's Authorization Callback Domain,
        # so this must be the host the browser is using (localhost:PORT).
        host = self.headers.get("Host") or "localhost:8000"
        self._redirect(strava.authorize_url(
            auth["client_id"], "http://%s/strava/callback" % host))

    def _strava_callback(self, q):
        if q.get("error"):
            return self._html_message(400, "Strava not connected",
                                      "Strava said: %s" % q["error"][0])
        code = (q.get("code") or [""])[0]
        scope = (q.get("scope") or [""])[0]
        if not code:
            return self._html_message(400, "Strava not connected",
                                      "No authorization code came back.")
        if "activity:read" not in scope:
            return self._html_message(400, "Strava not connected",
                                      "Activity access wasn't granted. Connect "
                                      "again and leave \"View data about your "
                                      "activities\" checked.")
        conn = self._db()
        try:
            auth = db.strava_auth(conn)
            tok = strava.exchange_code(auth["client_id"], auth["client_secret"], code)
            self._save_strava_tokens(conn, tok)
        except RuntimeError as e:
            return self._html_message(502, "Strava not connected", str(e))
        finally:
            conn.close()
        self._redirect("/#settings")

    @staticmethod
    def _save_strava_tokens(conn, tok):
        updates = {"strava_access_token": tok["access_token"],
                   "strava_refresh_token": tok["refresh_token"],
                   "strava_expires_at": str(tok["expires_at"])}
        ath = tok.get("athlete") or {}
        if ath:
            updates["strava_athlete"] = " ".join(
                x for x in (ath.get("firstname"), ath.get("lastname")) if x)
        db.save_settings(conn, updates)

    def _strava_token(self, conn):
        """Current access token, refreshed if it's about to expire; None
        when Strava isn't connected."""
        auth = db.strava_auth(conn)
        if not auth["refresh_token"]:
            return None
        if strava.needs_refresh(auth["expires_at"]):
            tok = strava.refresh(auth["client_id"], auth["client_secret"],
                                 auth["refresh_token"])
            self._save_strava_tokens(conn, tok)
            return tok["access_token"]
        return auth["access_token"]

    def _api_strava_credentials(self):
        conn = self._db()
        try:
            body = self._json_body()
            updates = {"strava_client_id": str(body.get("client_id") or "").strip()}
            # blank secret = keep the saved one
            if (body.get("client_secret") or "").strip():
                updates["strava_client_secret"] = body["client_secret"].strip()
            db.save_settings(conn, updates)
            return self._send(200, {"ok": True})
        finally:
            conn.close()

    def _api_strava_disconnect(self):
        conn = self._db()
        try:
            db.save_settings(conn, {"strava_access_token": "",
                                    "strava_refresh_token": "",
                                    "strava_expires_at": "", "strava_athlete": ""})
            return self._send(200, {"ok": True})
        finally:
            conn.close()

    def _api_strava_import(self):
        conn = self._db()
        try:
            body = self._json_body()
            since = body.get("since") or "2026-08-01"
            after = int(datetime.datetime.fromisoformat(since).timestamp())
            token = self._strava_token(conn)
            if not token:
                return self._send(400, {"error": "Connect Strava in Settings first"})
            imported, skipped, laps_added, note = 0, 0, 0, None
            have = {r[0]: r[1] for r in conn.execute(
                "SELECT source_file, id FROM activities WHERE source_file LIKE 'strava:%'")}
            for summary in strava.fetch_activities(token, after):
                if not strava.is_run(summary):
                    skipped += 1
                    continue
                aid = have.get("strava:%s" % summary.get("id"))
                if aid:
                    # already imported: backfill laps if it predates lap import
                    try:
                        if self._backfill_laps(token, aid, summary["id"]):
                            laps_added += 1
                        else:
                            skipped += 1
                    except strava.RateLimited:
                        note = ("Strava rate limit reached; wait 15 minutes and "
                                "import again to fetch the rest.")
                        break
                    except Exception:
                        traceback.print_exc()
                        skipped += 1
                    continue
                try:
                    streams = strava.fetch_streams(token, summary["id"])
                    points = strava.streams_to_points(summary, streams)
                    if len(points) < 30:
                        skipped += 1
                        continue
                    laps = strava.fetch_laps(token, summary["id"])
                    act = strava.to_activity(summary, points, laps)
                    # keep the raw streams so settings changes can reclassify
                    raw = json.dumps({"summary": summary, "streams": streams,
                                      "laps": laps}).encode()
                    aid = self._classify_and_store(conn, act, "strava.json", raw)
                    if aid:
                        imported += 1
                    else:
                        skipped += 1
                except strava.RateLimited:
                    note = ("Strava rate limit reached; wait 15 minutes and "
                            "import again to fetch the rest.")
                    break
                except Exception:
                    traceback.print_exc()
                    skipped += 1
            if laps_added:
                self._reclassify_all(conn)
            else:
                analysis.refresh_weeks(conn)
            return self._send(200, {"imported": imported, "skipped": skipped,
                                    "laps_added": laps_added, "note": note})
        except RuntimeError as e:
            return self._send(502, {"error": str(e)})
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    os.makedirs(UPLOADS, exist_ok=True)
    conn = db.connect(os.path.join(DATA, "dashboard.db"))
    # Data stored before per-rep max HR / timer time existed: reclassify
    # once to fill them in.
    if conn.execute("SELECT 1 FROM bouts WHERE avg_hr IS NOT NULL "
                    "AND max_hr IS NULL LIMIT 1").fetchone() or \
            conn.execute("SELECT 1 FROM activities WHERE timer_s IS NULL "
                         "LIMIT 1").fetchone():
        print("Updating stored workouts (one-time)...")
        Handler._reclassify_all(conn)
    else:
        analysis.refresh_weeks(conn)
    conn.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("Singles dashboard running at http://localhost:%d" % port)
    print("Data: %s" % DATA)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
