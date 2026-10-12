import redis
from apscheduler.schedulers.background import BackgroundScheduler
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

# Used only in development (python run.py). Production runs scheduler_worker.py.
scheduler = BackgroundScheduler(
    timezone="Africa/Lagos",
    job_defaults={"coalesce": True, "misfire_grace_time": 3600},
)

# Rate limiting. Storage comes from RATELIMIT_STORAGE_URI in config
# (Redis in production, memory in development).
limiter = Limiter(key_func=get_remote_address)

class _Redis:
    """Shared Redis clients, set up in create_app via rds.init_app(app)."""

    def __init__(self):
        self._client = None
        self._raw = None

    def init_app(self, app):
        opts = dict(socket_timeout=2, socket_connect_timeout=2, health_check_interval=30)
        url = app.config["REDIS_URL"]
        self._client = redis.Redis.from_url(url, decode_responses=True, **opts)   # text values
        self._raw = redis.Redis.from_url(url, **opts)                             # bytes (cached objects)

    @property
    def client(self):
        if self._client is None:
            raise RuntimeError("Redis is not initialised; call rds.init_app(app) in create_app.")
        return self._client

    @property
    def raw(self):
        if self._raw is None:
            raise RuntimeError("Redis is not initialised; call rds.init_app(app) in create_app.")
        return self._raw

rds = _Redis()

# Redirect unauthenticated users to the landing page
login_manager.login_view = "auth.landing"
login_manager.login_message = "Please sign in to access this page."
login_manager.login_message_category = "warning"