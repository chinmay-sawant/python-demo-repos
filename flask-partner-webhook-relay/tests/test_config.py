import os

from app.config import Config
from app.database import db


def test_default_database_url_is_postgres():
    assert Config.SQLALCHEMY_DATABASE_URI == os.getenv(
        "DATABASE_URL", "postgresql://postgres@localhost/relay"
    )


def test_engine_options_pooling():
    options = Config.SQLALCHEMY_ENGINE_OPTIONS
    assert options["pool_size"] == 10
    assert options["max_overflow"] == 5
    assert options["pool_pre_ping"] is True


def test_tests_run_on_sqlite(app):
    with app.app_context():
        assert db.engine.dialect.name == "sqlite"
