from flask import Blueprint

recovery_bp = Blueprint("recovery", __name__)

from app.blueprints.recovery import routes  # noqa: E402, F401