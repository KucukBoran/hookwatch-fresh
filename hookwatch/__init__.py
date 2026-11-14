import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from flask import (
    Flask,
    g,
    request,
    jsonify,
    render_template,
    session,
    redirect,
    url_for,
    flash,
    Response,
)
from werkzeug.exceptions import HTTPException
from .config import settings
from .database import connect, initialize, transaction
from .security import verify
from .service import Problem, STATUSES, ingest, process_next, retry, summary


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(settings())
    if config:
        app.config.update(config)
    for key in ("SECRET_KEY", "ADMIN_TOKEN", "WEBHOOK_SECRET"):
        if len(app.config.get(key, "")) < 20:
            raise RuntimeError(
                f"Set {key} to at least 20 characters. Copy .env.example for a local demo."
            )
    initialize(app.config["DATABASE"])

    def now():
        return app.config.get("CLOCK", time.time)()

    @app.template_filter("utc")
    def utc(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime("%d %b, %H:%M:%S")

    @app.template_filter("prettyjson")
    def prettyjson(value):
        return json.dumps(json.loads(value), indent=2, ensure_ascii=False)

    @app.context_processor
    def shared():
        return {
            "csrf": session.get("csrf", ""),
            "demo_mode": app.config["DEMO_MODE"],
            "statuses": STATUSES,
        }

    @app.before_request
    def authorize():
        if request.endpoint == "static":
            return None
        g.db = connect(app.config["DATABASE"])
        session.setdefault("csrf", secrets.token_hex(32))
        if request.endpoint in ("events_api", "detail_api", "metrics"):
            expected = f"Bearer {app.config['ADMIN_TOKEN']}"
            if not hmac.compare_digest(
                request.headers.get("Authorization", "").encode(), expected.encode()
            ):
                raise Problem(401, "A valid admin bearer token is required")
        elif request.endpoint not in ("login", "health", "ingest_route", None):
            if not session.get("signed_in"):
                return redirect(url_for("login"))
        if request.method == "POST" and request.endpoint != "ingest_route":
            if not hmac.compare_digest(
                request.form.get("csrf", "").encode(), session["csrf"].encode()
            ):
                raise Problem(403, "Form expired. Refresh and try again.")

    @app.teardown_appcontext
    def close_db(error=None):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.after_request
    def headers(response):
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Cache-Control": "no-store",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'",
            }
        )
        return response

    @app.errorhandler(Problem)
    def problem(error):
        if request.path.startswith("/api/") or request.path == "/metrics":
            return jsonify(error=str(error)), error.status
        return render_template(
            "error.html", code=error.status, message=str(error)
        ), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/") or request.path == "/metrics":
            return jsonify(error=error.description), error.code
        return render_template(
            "error.html", code=error.code, message=error.description
        ), error.code

    @app.get("/health")
    def health():
        g.db.execute("SELECT 1")
        return jsonify(status="ok")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("signed_in"):
            return redirect(url_for("dashboard"))
        error = None
        if request.method == "POST":
            client = hashlib.sha256(
                (request.remote_addr or "unknown").encode()
            ).hexdigest()
            timestamp = now()
            with transaction(g.db):
                g.db.execute(
                    "DELETE FROM login_attempts WHERE reset_at<=?", (timestamp,)
                )
                attempt = g.db.execute(
                    "SELECT failures FROM login_attempts WHERE client=?", (client,)
                ).fetchone()
                if attempt and attempt["failures"] >= 10:
                    raise Problem(429, "Too many attempts. Try again in 15 minutes.")
                token = request.form.get("token", "")
                valid = hmac.compare_digest(
                    token.encode(), app.config["ADMIN_TOKEN"].encode()
                )
                if not valid:
                    g.db.execute(
                        "INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET failures=failures+1",
                        (client, timestamp + 900),
                    )
                else:
                    g.db.execute("DELETE FROM login_attempts WHERE client=?", (client,))
            if valid:
                session.clear()
                session.update(signed_in=True, csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect(url_for("dashboard"), 303)
            error = "The admin token is incorrect."
        return render_template("login.html", error=error), 401 if error else 200

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), 303)

    @app.post("/api/events")
    def ingest_route():
        if not request.is_json:
            raise Problem(415, "Send application/json")
        raw = request.get_data()
        if not verify(
            app.config["WEBHOOK_SECRET"],
            request.headers.get("X-Webhook-Timestamp", ""),
            request.headers.get("X-Webhook-Signature", ""),
            raw,
            now(),
        ):
            raise Problem(
                401,
                "Signature is invalid or timestamp is outside the five-minute window",
            )
        payload = request.get_json()
        event_id, created = ingest(g.db, payload, now(), app.config["DEMO_MODE"])
        return jsonify(
            id=event_id,
            duplicate=not created,
            url=url_for("detail_api", event_id=event_id),
        ), 201 if created else 200

    def get_event(event_id):
        event = g.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if not event:
            raise Problem(404, "Event not found")
        return event

    def listing():
        status = request.args.get("status", "all")
        source = request.args.get("source", "")[:128]
        if status != "all" and status not in STATUSES:
            raise Problem(422, "Unknown status")
        try:
            page = int(request.args.get("page", 1))
            if not 1 <= page <= 100000:
                raise ValueError()
        except ValueError:
            raise Problem(422, "page must be a positive integer")
        where, args = ["1=1"], []
        if status != "all":
            where.append("status=?")
            args.append(status)
        if source:
            where.append("source=?")
            args.append(source)
        condition = " AND ".join(where)
        total = g.db.execute(
            f"SELECT COUNT(*) FROM events WHERE {condition}", args
        ).fetchone()[0]
        rows = g.db.execute(
            f"SELECT * FROM events WHERE {condition} ORDER BY received_at DESC,id DESC LIMIT 20 OFFSET ?",
            [*args, (page - 1) * 20],
        ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "status": status,
            "source": source,
        }

    @app.get("/api/events")
    def events_api():
        return jsonify(listing())

    @app.get("/api/events/<int:event_id>")
    def detail_api(event_id):
        event = dict(get_event(event_id))
        event["payload"] = json.loads(event["payload"])
        event.pop("lease_token", None)
        return jsonify(event)

    @app.get("/")
    def dashboard():
        timestamp = now()
        lower = int(timestamp // 3600) * 3600 - 11 * 3600
        points = [0] * 12
        for row in g.db.execute(
            "SELECT CAST((received_at-?)/3600 AS INTEGER) bucket,COUNT(*) n FROM events WHERE received_at>=? AND received_at<=? GROUP BY bucket",
            (lower, lower, timestamp),
        ):
            if 0 <= row["bucket"] < 12:
                points[row["bucket"]] = row["n"]
        max_point = max(points) or 1
        chart = [
            {
                "hour": datetime.fromtimestamp(lower + i * 3600, timezone.utc).strftime(
                    "%H:%M"
                ),
                "count": count,
                "height": round(count / max_point * 100),
            }
            for i, count in enumerate(points)
        ]
        return render_template(
            "dashboard.html",
            listing=listing(),
            stats=summary(g.db),
            chart=chart,
            sources=[
                row[0]
                for row in g.db.execute(
                    "SELECT DISTINCT source FROM events ORDER BY source"
                )
            ],
        )

    @app.get("/events/<int:event_id>")
    def detail(event_id):
        event = get_event(event_id)
        attempts = g.db.execute(
            "SELECT * FROM attempts WHERE event_id=? ORDER BY id DESC", (event_id,)
        ).fetchall()
        actions = g.db.execute(
            "SELECT * FROM actions WHERE event_id=? ORDER BY id DESC", (event_id,)
        ).fetchall()
        return render_template(
            "detail.html", event=event, attempts=attempts, actions=actions
        )

    @app.post("/events/<int:event_id>/retry")
    def retry_route(event_id):
        retry(g.db, event_id, now())
        flash("Event requeued. Previous attempts are preserved.")
        return redirect(url_for("detail", event_id=event_id), 303)

    @app.post("/worker/tick")
    def tick():
        processed = process_next(g.db, now())
        flash(
            "Processed one eligible event."
            if processed
            else "No events are due. Retrying events may still be in backoff."
        )
        return redirect(url_for("dashboard"), 303)

    @app.post("/demo-event")
    def demo_event():
        if not app.config["DEMO_MODE"]:
            raise Problem(404, "Demo mode is disabled")
        payload = {
            "id": "demo_" + secrets.token_hex(8),
            "source": "demo-console",
            "type": "release.created",
            "data": {"release_id": "demo-release", "title": "A fresh signal"},
            "simulate_failures": 1,
        }
        ingest(g.db, payload, now(), demo=True)
        flash("Demo event queued. Its first processor attempt will fail, then retry.")
        return redirect(url_for("dashboard"), 303)

    @app.get("/metrics")
    def metrics():
        stats = summary(g.db)
        lines = [
            "# HELP hookwatch_events Current stored events by status.",
            "# TYPE hookwatch_events gauge",
        ]
        lines += [
            f'hookwatch_events{{status="{status}"}} {stats["counts"][status]}'
            for status in STATUSES
        ]
        lines += [
            "# HELP hookwatch_attempts_total Completed processing attempts.",
            "# TYPE hookwatch_attempts_total counter",
        ]
        for outcome in ("success", "error"):
            count = g.db.execute(
                "SELECT COUNT(*) FROM attempts WHERE outcome=?", (outcome,)
            ).fetchone()[0]
            lines.append(f'hookwatch_attempts_total{{outcome="{outcome}"}} {count}')
        count, duration = g.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(duration_seconds),0) FROM attempts"
        ).fetchone()
        lines += [
            "# HELP hookwatch_process_duration_seconds Processing time for completed attempts.",
            "# TYPE hookwatch_process_duration_seconds summary",
            f"hookwatch_process_duration_seconds_count {count}",
            f"hookwatch_process_duration_seconds_sum {duration}",
        ]
        return Response(
            "\n".join(lines) + "\n",
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app
