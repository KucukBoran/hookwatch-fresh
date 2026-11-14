import pytest
from hookwatch.service import (
    ingest,
    process_next,
    claim,
    finish,
    retry,
    summary,
    Problem,
)


def test_success_has_a_persisted_result(db, payload, clock):
    event_id, _ = ingest(db, payload, clock["now"])
    assert process_next(db, clock["now"])
    row = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    assert row["status"] == "succeeded" and row["attempts"] == 1
    assert "A sample release" in row["result"]
    assert summary(db)["success_rate"] == 100


def test_backoff_delays_retry_then_succeeds(db, payload, clock):
    event_id, _ = ingest(
        db, {**payload, "simulate_failures": 1}, clock["now"], demo=True
    )
    process_next(db, clock["now"])
    assert (
        db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0]
        == "retrying"
    )
    assert not process_next(db, clock["now"] + 9)
    assert process_next(db, clock["now"] + 10)
    assert (
        db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0]
        == "succeeded"
    )
    assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 2


def test_dead_letter_and_manual_retry_keep_history(db, payload, clock):
    event_id, _ = ingest(
        db, {**payload, "simulate_failures": 3}, clock["now"], demo=True
    )
    for offset in (0, 10, 30):
        assert process_next(db, clock["now"] + offset)
    assert db.execute("SELECT status FROM events").fetchone()[0] == "failed"
    retry(db, event_id, clock["now"] + 31)
    assert db.execute("SELECT status,attempts FROM events").fetchone()[:] == (
        "queued",
        0,
    )
    assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 3
    assert db.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2


def test_retry_of_successful_event_is_rejected(db, payload, clock):
    event_id, _ = ingest(db, payload, clock["now"])
    process_next(db, clock["now"])
    with pytest.raises(Problem) as error:
        retry(db, event_id, clock["now"])
    assert error.value.status == 409


def test_worker_crash_lease_recovery_and_stale_completion(db, payload, clock):
    ingest(db, payload, clock["now"])
    old = claim(db, clock["now"])
    assert claim(db, clock["now"] + 29) is None
    new = claim(db, clock["now"] + 30)
    assert new["attempts"] == 2 and old["lease_token"] != new["lease_token"]
    assert not finish(db, old, "Stale result", None, 0.01, clock["now"] + 31)
    assert finish(db, new, "Fresh result", None, 0.01, clock["now"] + 31)
    assert db.execute("SELECT result FROM events").fetchone()[0] == "Fresh result"


def test_final_expired_lease_moves_to_failed(db, payload, clock):
    ingest(db, payload, clock["now"])
    for offset in (0, 30, 60):
        assert claim(db, clock["now"] + offset)
    assert claim(db, clock["now"] + 90) is None
    assert db.execute("SELECT status FROM events").fetchone()[0] == "failed"


def test_empty_queue_has_no_success_rate(db):
    assert not process_next(db)
    assert summary(db)["success_rate"] is None
