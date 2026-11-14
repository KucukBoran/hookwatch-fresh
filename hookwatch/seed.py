"""Create fictional events with representative outcomes. Never resets an existing DB."""

import time
from .service import ingest, process_next


def seed(db):
    if db.execute("SELECT COUNT(*) FROM events").fetchone()[0]:
        return False
    now = time.time()
    sources = ["release-api", "report-importer", "catalog-sync"]
    for i in range(24):
        timestamp = now - (24 - i) * 1400
        index = i % 3
        kind = ["release.created", "report.received", "catalog.updated"][index]
        data = [
            {
                "release_id": f"rel_{i:03}",
                "title": ["Blue Hour", "Night Transit", "Soft Signal"][i % 3],
            },
            {"report_id": f"report_{i:03}", "rows": 120 + i * 7},
            {"artist": "Parallel Bloom", "tracks": 8 + i},
        ][index]
        payload = {
            "id": f"evt_demo_{i:04}",
            "source": sources[index],
            "type": kind,
            "data": data,
        }
        if i in (9, 18, 23):
            payload["simulate_failures"] = 3 if i == 9 else 1
        event_id, _ = ingest(db, payload, timestamp, demo=True)
        if i < 22:
            process_next(db, timestamp + 0.1)
            process_next(db, timestamp + 11)
            process_next(db, timestamp + 32)
    # Latest examples remain queued so the dashboard demonstrates the worker.
    return True
