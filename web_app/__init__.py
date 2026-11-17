"""Web app factory. Registers all blueprints."""
from __future__ import annotations

from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    from .blueprints.runs import bp as runs_bp
    from .blueprints.people import bp as people_bp
    from .blueprints.ancestors import bp as ancestors_bp
    from .blueprints.results import bp as results_bp

    app.register_blueprint(runs_bp)
    app.register_blueprint(people_bp)
    app.register_blueprint(ancestors_bp)
    app.register_blueprint(results_bp)

    return app
