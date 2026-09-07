from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
scheduler = BackgroundScheduler()

# Redirect unauthenticated users to the landing page
login_manager.login_view = "auth.landing"
login_manager.login_message = "Please sign in to access this page."
login_manager.login_message_category = "warning"
