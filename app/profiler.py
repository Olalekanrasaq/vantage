import time
from collections import Counter

from flask import g, request, has_request_context
from sqlalchemy import event

from app.extensions import db


def init_query_profiler(app):
    """Logs query count and DB time per request, plus any statement repeated 5+ times (N+1 signal)."""
    with app.app_context():
        engine = db.engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, params, context, executemany):
        conn.info.setdefault("qt", []).append(time.perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, params, context, executemany):
        elapsed_ms = (time.perf_counter() - conn.info["qt"].pop()) * 1000
        if not has_request_context():
            return
        g.q_count = getattr(g, "q_count", 0) + 1
        g.q_ms = getattr(g, "q_ms", 0.0) + elapsed_ms
        if not hasattr(g, "q_stmts"):
            g.q_stmts = Counter()
        g.q_stmts[statement] += 1

    @app.after_request
    def _report(resp):
        if request.path.startswith("/static"):
            return resp
        app.logger.info("%s %s -> %d queries, %.0f ms in DB",
                        request.method, request.path,
                        getattr(g, "q_count", 0), getattr(g, "q_ms", 0.0))
        for stmt, n in getattr(g, "q_stmts", Counter()).most_common(3):
            if n >= 5:
                app.logger.info("    repeated %dx: %s", n, " ".join(stmt.split())[:120])
        return resp