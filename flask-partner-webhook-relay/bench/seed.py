import os

os.environ.setdefault("DATABASE_URL", "sqlite:///bench_relay.db")

from app import create_app
from app.database import db
from app.models import Partner, PartnerEndpoint

app = create_app()

with app.app_context():
    partner = Partner(name="Bench Partner", api_key_hash="bench-hash", is_active=True)
    db.session.add(partner)
    db.session.flush()
    endpoint = PartnerEndpoint(
        partner_id=partner.id,
        url="http://127.0.0.1:8200/webhook",
        secret="bench-secret",
        is_active=True,
    )
    db.session.add(endpoint)
    db.session.commit()
    print(f"seeded partner={partner.id}, endpoint={endpoint.id} -> {endpoint.url}")
