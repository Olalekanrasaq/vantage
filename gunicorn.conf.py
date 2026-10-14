import os

bind = os.getenv("GUNICORN_BIND") or f"0.0.0.0:{os.getenv('PORT', '8000')}"
workers = int(os.getenv("WEB_WORKERS", 3))
worker_class = "gthread"
threads = int(os.getenv("WEB_THREADS", 4))   # keep equal to pool_size in ProductionConfig

timeout = 120             # PDF extraction and AI calls can take a while
graceful_timeout = 30
keepalive = 5

max_requests = 1000       # recycle workers to cap memory growth from PDF parsing
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = "info"

# Do NOT set preload_app = True: each worker must build its own app and DB pool after forking.