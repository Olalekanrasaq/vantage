import os
from app import create_app

app = create_app(os.environ.get("FLASK_ENV", "development"))

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
    # use_reloader=False prevents APScheduler from starting twice in debug mode
