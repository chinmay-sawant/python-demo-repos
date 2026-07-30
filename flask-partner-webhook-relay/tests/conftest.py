import pytest
from app import create_app
from app.database import db as _db
from app.models import Partner, PartnerEndpoint, InboundEvent, DeliveryOutbox, DeliveryAttempt

@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["INGEST_API_KEY"] = "test-api-key"

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
