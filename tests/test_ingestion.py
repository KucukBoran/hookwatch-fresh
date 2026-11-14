from concurrent.futures import ThreadPoolExecutor
import json
from hookwatch.database import connect
from hookwatch.security import sign
from hookwatch.service import ingest


def send(app, client, payload, timestamp, raw=None, signature=None):
    raw = raw if raw is not None else json.dumps(payload).encode()
    timestamp = str(int(timestamp))
    return client.post(
        "/api/events",
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": signature
            or sign(app.config["WEBHOOK_SECRET"], timestamp, raw),
        },
    )


def test_signed_event_is_durable_and_duplicate_is_idempotent(
    app, client, db, payload, clock
):
    first = send(app, client, payload, clock["now"])
    second = send(
        app, client, payload, clock["now"], raw=json.dumps(payload, indent=2).encode()
    )
    assert first.status_code == 201
    assert second.status_code == 200 and second.json["duplicate"]
    assert first.json["id"] == second.json["id"]
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_reusing_id_with_different_payload_conflicts(app, client, payload, clock):
    assert send(app, client, payload, clock["now"]).status_code == 201
    changed = {**payload, "data": {**payload["data"], "title": "Different"}}
    assert send(app, client, changed, clock["now"]).status_code == 409


def test_bad_or_expired_signatures_never_persist(app, client, db, payload, clock):
    assert (
        send(
            app, client, payload, clock["now"], signature="sha256=" + "0" * 64
        ).status_code
        == 401
    )
    assert send(app, client, payload, clock["now"] - 301).status_code == 401
    assert send(app, client, payload, clock["now"] + 301).status_code == 401
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_valid_signature_does_not_bypass_schema(app, client, payload, clock):
    for invalid in [
        [],
        {},
        {**payload, "type": "unknown"},
        {**payload, "data": {}},
        {**payload, "simulate_failures": 1},
    ]:
        assert send(app, client, invalid, clock["now"]).status_code == 422
    invalid = {
        **payload,
        "type": "report.received",
        "data": {"report_id": "r1", "rows": True},
    }
    assert send(app, client, invalid, clock["now"]).status_code == 422


def test_json_errors_and_size_limits(app, client, clock):
    assert client.post("/api/events", data="text").status_code == 415
    assert send(app, client, None, clock["now"], raw=b"{invalid").status_code == 400
    assert (
        client.post(
            "/api/events",
            data=b"a" * 70000,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_concurrent_duplicates_create_one_event(app, db, payload, clock):
    def submit(_):
        connection = connect(app.config["DATABASE"])
        try:
            return ingest(connection, payload, clock["now"])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(submit, range(8)))
    assert sum(created for _, created in outcomes) == 1
    assert len({event_id for event_id, _ in outcomes}) == 1


def test_event_identity_is_scoped_by_source(app, client, payload, clock):
    assert send(app, client, payload, clock["now"]).status_code == 201
    assert (
        send(
            app, client, {**payload, "source": "other-source"}, clock["now"]
        ).status_code
        == 201
    )
