"""App factory: creates Flask app, initializes extensions, registers blueprints."""
from flask import Flask

from app.extensions import db, login_manager
from app import config as app_config


def create_app():
    app = Flask(__name__)
    app.config.from_object(app_config)

    db.init_app(app)
    login_manager.init_app(app)

    from app.auth.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.auth.routes import bp as auth_bp
    from app.dashboard.routes import bp as dashboard_bp
    from app.evaluator.routes import bp as evaluator_bp
    from app.game.routes import bp as game_bp
    from app.rooms.routes import bp as rooms_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(evaluator_bp)
    app.register_blueprint(game_bp)
    app.register_blueprint(rooms_bp)

    return app
