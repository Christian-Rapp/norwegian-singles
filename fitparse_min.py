"""Minimal FIT file parser (stdlib only).

Extracts what the training dashboard needs from Garmin-style activity files:
  - record messages: timestamp, position, altitude, heart_rate, distance
  - lap messages: elapsed time, distance, avg/max HR
  - session message: totals
  - file_id message: creation time

Handles normal + compressed-timestamp record headers, both endiannesses,
and skips developer data fields. Not a full FIT SDK — unknown messages and
fields are ignored.
"""

import struct

FIT_EPOCH_OFFSET = 631065600  # seconds between FIT epoch (1989-12-31) and Unix epoch
SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)

# global message numbers we care about
MSG_FILE_ID = 0
MSG_RECORD = 20
MSG_LAP = 19
MSG_SESSION = 18

# base type number -> (struct char or None, size in bytes); masked with 0x1F
_BASE_TYPES = {
    0: ("B", 1),    # enum
    1: ("b", 1),    # sint8
    2: ("B", 1),    # uint8
    3: ("h", 2),    # sint16
    4: ("H", 2),    # uint16
    5: ("i", 4),    # sint32
    6: ("I", 4),    # uint32
    7: (None, None),  # string (variable)
    8: ("f", 4),    # float32
    9: ("d", 8),    # float64
    10: ("B", 1),   # uint8z
    11: ("H", 2),   # uint16z
    12: ("I", 4),   # uint32z
    13: ("B", 1),   # byte
    14: ("q", 8),   # sint64
    15: ("Q", 8),   # uint64
    16: ("Q", 8),   # uint64z
}

# invalid sentinel per byte-width (covers the integer types we use)
_INVALID = {1: (0xFF,), 2: (0xFFFF, 0x7FFF), 4: (0xFFFFFFFF, 0x7FFFFFFF),
            8: (0xFFFFFFFFFFFFFFFF, 0x7FFFFFFFFFFFFFFF)}


def _is_invalid(value, size):
    return value in _INVALID.get(size, ())


class FitParseError(Exception):
    pass


def _read_uint(data, pos, size, endian):
    fmt = {1: "B", 2: "H", 4: "I", 8: "Q"}[size]
    return struct.unpack(endian + fmt, data[pos:pos + size])[0]


def parse(data):
    """Parse FIT bytes. Returns dict with records, laps, session, file_id_time."""
    if len(data) < 14:
        raise FitParseError("too short for FIT header")
    header_size = data[0]
    if header_size not in (12, 14):
        raise FitParseError("bad FIT header size: %r" % header_size)
    if data[8:12] != b".FIT":
        raise FitParseError("missing .FIT marker")
    data_size = _read_uint(data, 4, 4, "<")
    body = data[header_size:header_size + data_size]
    if len(body) < data_size:
        raise FitParseError("truncated FIT body")

    records, laps = [], []
    session, file_id_time = {}, None
    definitions = {}  # local msg type -> (endian, global_num, [(field_num, size, base)])
    prev_timestamp = None

    pos = 0
    n = len(body)
    while pos < n:
        header = body[pos]
        pos += 1
        if header & 0x80:
            # compressed timestamp header
            local_type = (header >> 5) & 0x03
            offset = header & 0x1F
            if prev_timestamp is None:
                # shouldn't happen; skip gracefully
                continue
            timestamp = (prev_timestamp & ~0x1F) + offset
            if offset < (prev_timestamp & 0x1F):
                timestamp += 0x20  # rollover
            is_definition = False
            compressed_ts = timestamp
        else:
            is_definition = bool(header & 0x40)
            has_dev = bool(header & 0x20)
            local_type = header & 0x0F
            compressed_ts = None

        if is_definition:
            if pos + 5 > n:
                break
            # reserved = body[pos]; arch byte next
            arch = body[pos + 1]
            endian = "<" if arch == 0 else ">"
            global_num = _read_uint(body, pos + 2, 2, endian)
            num_fields = body[pos + 4]
            pos += 5
            fields = []
            for _ in range(num_fields):
                if pos + 3 > n:
                    break
                fnum, fsize, fbase = body[pos], body[pos + 1], body[pos + 2] & 0x1F
                pos += 3
                fields.append((fnum, fsize, fbase))
            if has_dev:
                if pos >= n:
                    break
                num_dev = body[pos]
                pos += 1 + 3 * num_dev
            definitions[local_type] = (endian, global_num, fields)
            continue

        # data message
        defin = definitions.get(local_type)
        if defin is None:
            # unknown local type; cannot know length — abort parse
            break
        endian, global_num, fields = defin
        values = {}
        ok = True
        for fnum, fsize, fbase in fields:
            if pos + fsize > n:
                ok = False
                break
            spec = _BASE_TYPES.get(fbase)
            if spec is None or spec[0] is None:
                # string or unknown: take raw bytes, strip NULs
                raw = body[pos:pos + fsize]
                try:
                    values[fnum] = raw.split(b"\x00")[0].decode("utf-8", "replace")
                except Exception:
                    values[fnum] = None
            else:
                fmt, size = spec
                v = struct.unpack(endian + fmt, body[pos:pos + size])[0]
                values[fnum] = None if _is_invalid(v, size) else v
            pos += fsize
        if not ok:
            break

        if global_num == MSG_RECORD:
            ts = values.get(253)
            if compressed_ts is not None:
                ts = compressed_ts
            if ts is not None:
                prev_timestamp = ts
            rec = {"t": (ts + FIT_EPOCH_OFFSET) if ts is not None else None}
            lat = values.get(0)
            lon = values.get(1)
            rec["lat"] = lat * SEMICIRCLE_TO_DEG if lat is not None else None
            rec["lon"] = lon * SEMICIRCLE_TO_DEG if lon is not None else None
            alt = values.get(2)
            rec["alt"] = (alt / 5.0 - 500.0) if alt is not None else None
            rec["hr"] = values.get(3)
            dist = values.get(5)
            rec["dist"] = dist / 100.0 if dist is not None else None
            records.append(rec)
        elif global_num == MSG_LAP:
            lap = {}
            ts = values.get(253)
            lap["t"] = (ts + FIT_EPOCH_OFFSET) if ts is not None else None
            el = values.get(7)
            lap["elapsed"] = el / 1000.0 if el is not None else None
            # lap timestamp (253) is the lap's end; field 2 is its start
            st = values.get(2)
            lap["end_t"] = lap["t"]
            if st is not None:
                lap["start_t"] = st + FIT_EPOCH_OFFSET
            elif lap["t"] is not None and lap["elapsed"] is not None:
                lap["start_t"] = lap["t"] - lap["elapsed"]
            else:
                lap["start_t"] = None
            d = values.get(9)
            lap["distance"] = d / 100.0 if d is not None else None
            lap["avg_hr"] = values.get(15)
            lap["max_hr"] = values.get(16)
            laps.append(lap)
        elif global_num == MSG_SESSION:
            el = values.get(7)
            session["elapsed"] = el / 1000.0 if el is not None else None
            tt = values.get(8)  # total_timer_time: excludes pauses
            session["timer"] = tt / 1000.0 if tt is not None else None
            d = values.get(9)
            session["distance"] = d / 100.0 if d is not None else None
            session["avg_hr"] = values.get(16)
            session["max_hr"] = values.get(17)
            session["sport"] = values.get(5)
        elif global_num == MSG_FILE_ID:
            tc = values.get(4)
            if tc is not None:
                file_id_time = tc + FIT_EPOCH_OFFSET

    return {"records": records, "laps": laps, "session": session,
            "file_id_time": file_id_time}
