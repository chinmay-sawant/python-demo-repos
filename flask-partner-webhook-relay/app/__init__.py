from flask import Flask
from app.config import Config
from app.database import db, init_db

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    init_db(app)
    from app.routes import bp
    app.register_blueprint(bp)
    from app.cli import register_commands
    register_commands(app)
    return app
