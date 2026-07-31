import threading
from http.server import ThreadingHTTPServer
from typing import ClassVar

import pytest
from app import create_app
from app.config import Config
from app.database import db as _db
from app.models import Partner, PartnerEndpoint

from tests.helpers import MockPartnerHandler


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, object]] = {}
    INGEST_API_KEY = "test-api-key"


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def worker_app(tmp_path):
    class WorkerTestConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'worker.db'}"

    app = create_app(WorkerTestConfig)
    with app.app_context():
        _db.create_all()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture
def sample_partner(db):
    partner = Partner(name="Test Partner", api_key_hash="hash123", is_active=True)
    db.session.add(partner)
    db.session.flush()
    ep = PartnerEndpoint(
        partner_id=partner.id,
        url="https://partner.example.com/webhook",
        secret="secret123",
        is_active=True,
    )
    db.session.add(ep)
    db.session.commit()
    return partner


@pytest.fixture
def mock_partner():
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockPartnerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/webhook"
    server.shutdown()
