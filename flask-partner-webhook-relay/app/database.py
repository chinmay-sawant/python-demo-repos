from typing import TypeAlias

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)

Model: TypeAlias = db.Model  # type: ignore[name-defined]


def init_db(app):
    db.init_app(app)
    with app.app_context():
        db.create_all()
