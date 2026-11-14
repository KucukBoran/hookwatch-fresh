"""Persist first, then process. The processor has no external side effects."""

import hashlib
import json
import re
import time
import uuid
from .database import transaction

STATUSES = ("queued", "processing", "retrying", "succeeded", "failed")
EVENT_TYPES = ("release.created", "report.received", "catalog.updated")


class Problem(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def validate(payload, demo=False):
    if not isinstance(payload, dict):
        raise Problem(422, "Payload must be a JSON object")
    for field in ("id", "source"):
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value
        ):
            raise Problem(422, f"{field} must contain 1–128 identifier characters")
    if payload.get("type") not in EVENT_TYPES:
        raise Problem(422, "Unsupported event type")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise Problem(422, "data must be an object")
    fields = {
        "release.created": ("release_id", "title"),
        "report.received": ("report_id",),
        "catalog.updated": ("artist",),
    }[payload["type"]]
    for field in fields:
        if (
            not isinstance(data.get(field), str)
            or not 1 <= len(data[field].strip()) <= 200
        ):
            raise Problem(422, f"data.{field} must contain 1–200 characters")
    number_field = {"report.received": "rows", "catalog.updated": "tracks"}.get(
        payload["type"]
    )
    if number_field and (
        type(data.get(number_field)) is not int
        or not 0 <= data[number_field] <= 1_000_000
    ):
        raise Problem(422, f"data.{number_field} must be an integer from 0 to 1000000")
    failures = payload.get("simulate_failures", 0)
    if type(failures) is not int or not 0 <= failures <= 3 or (failures and not demo):
        raise Problem(422, "simulate_failures is only available in demo mode (0–3)")
    return payload


def ingest(db, payload, now=None, demo=False):
    validate(payload, demo)
    now = time.time() if now is None else now
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with transaction(db):
        existing = db.execute(
            "SELECT id,payload_hash FROM events WHERE source=? AND external_id=?",
            (payload["source"], payload["id"]),
        ).fetchone()
        if existing:
            if existing["payload_hash"] != digest:
                raise Problem(
                    409, "The same source and id already exist with a different payload"
                )
            return existing["id"], False
        cursor = db.execute(
            "INSERT INTO events(external_id,source,event_type,payload,payload_hash,next_attempt,received_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                payload["id"],
                payload["source"],
                payload["type"],
                canonical,
                digest,
                now,
                now,
                now,
            ),
        )
        event_id = cursor.lastrowid
        db.execute(
            "INSERT INTO actions(event_id,message,created_at) VALUES (?,?,?)",
            (event_id, "Accepted and queued", now),
        )
        return event_id, True


def claim(db, now=None):
    now = time.time() if now is None else now
    with transaction(db):
        # A crashed worker may have used its last attempt before its lease expired.
        exhausted = db.execute(
            "SELECT id FROM events WHERE status='processing' AND lease_until<=? AND attempts>=max_attempts",
            (now,),
        ).fetchall()
        for row in exhausted:
            db.execute(
                "UPDATE events SET status='failed',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            db.execute(
                "INSERT INTO actions(event_id,message,created_at) VALUES (?,?,?)",
                (row["id"], "Worker lease expired after final attempt", now),
            )
        event = db.execute(
            """SELECT * FROM events WHERE attempts<max_attempts AND
            ((status IN ('queued','retrying') AND next_attempt<=?) OR (status='processing' AND lease_until<=?))
            ORDER BY received_at,id LIMIT 1""",
            (now, now),
        ).fetchone()
        if event is None:
            return None
        lease = uuid.uuid4().hex
        if event["status"] == "processing":
            db.execute(
                "INSERT INTO actions(event_id,message,created_at) VALUES (?,?,?)",
                (event["id"], "Recovered an expired worker lease", now),
            )
        db.execute(
            "UPDATE events SET status='processing',attempts=attempts+1,lease_until=?,lease_token=?,updated_at=? WHERE id=?",
            (now + 30, lease, now, event["id"]),
        )
        return dict(
            db.execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone()
        )


def processor(event):
    payload = json.loads(event["payload"])
    if event["attempts"] <= payload.get("simulate_failures", 0):
        raise RuntimeError("Simulated temporary processor failure")
    data = payload["data"]
    if event["event_type"] == "release.created":
        return f"Release {data['release_id']} validated: {data['title']}"
    if event["event_type"] == "report.received":
        return f"Report {data['report_id']} accepted with {data['rows']} rows"
    return f"Catalog for {data['artist']} validated with {data['tracks']} tracks"


def finish(db, event, result, error, elapsed, now=None):
    now = time.time() if now is None else now
    with transaction(db):
        current = db.execute(
            "SELECT * FROM events WHERE id=?", (event["id"],)
        ).fetchone()
        if (
            current["status"] != "processing"
            or current["lease_token"] != event["lease_token"]
            or current["lease_until"] <= now
        ):
            return (
                False  # An expired worker must not overwrite a newer worker's result.
            )
        status = (
            "succeeded"
            if error is None
            else (
                "failed"
                if current["attempts"] >= current["max_attempts"]
                else "retrying"
            )
        )
        delay = 2 ** current["attempts"] * 5
        message = str(error)[:500] if error else result[:500]
        db.execute(
            "UPDATE events SET status=?,result=?,next_attempt=?,lease_until=NULL,lease_token=NULL,updated_at=? WHERE id=?",
            (status, message, now + delay if error else now, now, event["id"]),
        )
        db.execute(
            "INSERT INTO attempts(event_id,number,outcome,message,duration_seconds,created_at) VALUES (?,?,?,?,?,?)",
            (
                event["id"],
                current["attempts"],
                "error" if error else "success",
                message,
                elapsed,
                now,
            ),
        )
        return True


def process_next(db, now=None, handler=processor):
    event = claim(db, now)
    if not event:
        return False
    started = time.perf_counter()
    result, error = "", None
    try:
        result = handler(event)
    except Exception as exc:
        error = exc
    finish(db, event, result, error, time.perf_counter() - started, now)
    return True


def retry(db, event_id, now=None):
    now = time.time() if now is None else now
    with transaction(db):
        event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not event:
            raise Problem(404, "Event not found")
        if event["status"] != "failed":
            raise Problem(409, "Only failed events can be manually retried")
        db.execute(
            "UPDATE events SET status='queued',attempts=0,next_attempt=?,lease_until=NULL,lease_token=NULL,updated_at=? WHERE id=?",
            (now, now, event_id),
        )
        db.execute(
            "INSERT INTO actions(event_id,message,created_at) VALUES (?,?,?)",
            (event_id, "Manually requeued; attempt history preserved", now),
        )


def summary(db):
    counts = dict.fromkeys(STATUSES, 0)
    for row in db.execute("SELECT status,COUNT(*) n FROM events GROUP BY status"):
        counts[row["status"]] = row["n"]
    terminal = counts["succeeded"] + counts["failed"]
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "success_rate": round(counts["succeeded"] / terminal * 100, 1)
        if terminal
        else None,
    }
