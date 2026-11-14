import pytest
from hookwatch import create_app
from hookwatch.database import connect


@pytest.fixture
def clock():
    return {"now": 1794650400.0}


@pytest.fixture
def app(tmp_path, clock):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite"),
            "SECRET_KEY": "test-session-key-at-least-32-characters",
            "ADMIN_TOKEN": "test-admin-token-long-enough",
            "WEBHOOK_SECRET": "test-webhook-secret-long-enough",
            "DEMO_MODE": False,
            "CLOCK": lambda: clock["now"],
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    db = connect(app.config["DATABASE"])
    yield db
    db.close()


@pytest.fixture
def payload():
    return {
        "id": "evt_test_1",
        "source": "release-api",
        "type": "release.created",
        "data": {"release_id": "rel_1", "title": "A sample release"},
    }
