from flask import Blueprint

manager_bp = Blueprint("manager", __name__)

from app.blueprints.manager import routes  # noqa: E402, F401
