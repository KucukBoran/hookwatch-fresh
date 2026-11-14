import re
from hookwatch.service import ingest, process_next


def login(app, client):
    html = client.get("/login").text
    csrf = re.search('name="csrf" value="([a-f0-9]+)"', html).group(1)
    result = client.post(
        "/login", data={"csrf": csrf, "token": app.config["ADMIN_TOKEN"]}
    )
    assert result.status_code == 303
    with client.session_transaction() as session:
        return session["csrf"]


def test_private_dashboard_and_api_require_auth(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/events").status_code == 401
    assert client.get("/metrics").status_code == 401


def test_dashboard_detail_and_escaping(app, client, db, payload, clock):
    payload["data"]["title"] = "<script>alert(1)</script>"
    event_id, _ = ingest(db, payload, clock["now"])
    process_next(db, clock["now"])
    login(app, client)
    assert client.get("/").status_code == 200
    html = client.get(f"/events/{event_id}").text
    assert "&lt;script&gt;" in html and "<script>alert" not in html


def test_admin_actions_require_csrf_and_demo_mode(app, client):
    csrf = login(app, client)
    assert client.post("/worker/tick", data={}).status_code == 403
    assert client.post("/worker/tick", data={"csrf": csrf}).status_code == 303
    assert client.post("/demo-event", data={"csrf": csrf}).status_code == 404
    app.config["DEMO_MODE"] = True
    assert client.post("/demo-event", data={"csrf": csrf}).status_code == 303


def test_metrics_and_pagination(app, client, db, payload, clock):
    ingest(db, payload, clock["now"])
    process_next(db, clock["now"])
    headers = {"Authorization": f"Bearer {app.config['ADMIN_TOKEN']}"}
    response = client.get("/metrics", headers=headers)
    assert response.status_code == 200
    assert 'hookwatch_events{status="succeeded"} 1' in response.text
    assert 'hookwatch_attempts_total{outcome="success"} 1' in response.text
    assert client.get("/api/events?page=-1", headers=headers).status_code == 422
    assert client.get("/api/events?status=bogus", headers=headers).status_code == 422
    assert client.get("/api/events", headers=headers).json["total"] == 1


def test_unicode_tokens_rejected_and_logout_clears_session(app, client):
    html = client.get("/login").text
    csrf = re.search('name="csrf" value="([a-f0-9]+)"', html).group(1)
    assert (
        client.post("/login", data={"csrf": csrf, "token": "şifre"}).status_code == 401
    )
    csrf = login(app, client)
    assert client.post("/logout", data={"csrf": csrf}).status_code == 303
    assert client.get("/").status_code == 302
