"""FastAPI server of the Strauss car advisor.

Run:  uvicorn backend.main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agent.graph import AdvisorAgent
from .agent.llm import LLMClient, LLMError, load_env_file, make_llm, read_env_file
from .agent.state import make_state_store
from .api.auth import DemoAccess, LoginThrottle, load_secret
from .api.routes import SessionLocks, rep_router, router
from .data_loader.config_loader import get_config
from .data_loader.tax_loader import get_tax_db

log = logging.getLogger("strauss")

# Any localhost / 127.0.0.1 port (Vite 5173, CRA 3000, ...); more via CORS_ORIGINS=a,b
LOCAL_ORIGINS_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


MIN_REP_KEY_LEN = 16

SECURITY_HEADERS = {
    "Cache-Control": "no-store",            # responses carry salary data
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def create_app(db_path: Optional[Path] = None, llm: Optional[LLMClient] = None,
               rep_token: Optional[str] = None, secret_key: Optional[str] = None) -> FastAPI:
    """App factory; tests inject a temporary database, a deterministic LLM, a rep key and
    the token signing key."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_env_file()
        config = get_config()      # rules.json + challenge_data.xlsx + car_aliases.json
        tax_db = get_tax_db()      # builds/validates the indexed Tax Authority table
        # conversations DB: argument > DATABASE_URL (Postgres) > STRAUSS_SESSIONS_DB > app_data.db
        store = make_state_store(db_path)
        agent = AdvisorAgent(config, store, llm or make_llm(config))
        app.state.config, app.state.tax_db, app.state.agent = config, tax_db, agent
        app.state.session_locks = SessionLocks()
        # employee login: token signing key, demo access codes, wrong-code throttle
        app.state.secret_key = load_secret(secret_key)
        app.state.demo_access = DemoAccess()
        app.state.login_throttle = LoginThrottle()
        # representative / admin endpoints: shared secret, fail closed when missing
        app.state.rep_token = rep_token or os.environ.get("REP_API_KEY", "").strip() or None
        if app.state.rep_token is None:
            log.warning("REP_API_KEY is not set - the representative screen is closed")
        elif len(app.state.rep_token) < MIN_REP_KEY_LEN:
            log.warning("REP_API_KEY is shorter than %d characters - use a long random key",
                        MIN_REP_KEY_LEN)
        log.warning("loaded rules %s: %d cars, %d occupations, %d tax records | LLM: %s %s | "
                    "fallback: %s", config.rules.version, len(config.cars),
                    len(config.occupations), tax_db.count(), type(agent.llm).__name__,
                    getattr(agent.llm, "model", ""),
                    type(agent.fallback_llm).__name__ if agent.fallback_llm else "none")
        yield

    # interactive docs are handy locally; API_DOCS=0 turns them off (production)
    docs = os.environ.get("API_DOCS", read_env_file().get("API_DOCS", "1")).strip() != "0"
    app = FastAPI(title="יועץ הרכב של שטראוס", version="1.0", lifespan=lifespan,
                  docs_url="/docs" if docs else None, redoc_url=None,
                  openapi_url="/openapi.json" if docs else None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra,
        allow_origin_regex=LOCAL_ORIGINS_REGEX,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(rep_router)

    # An LLM failure becomes a clean 503 carrying the real cause (credits, quota, key,
    # model...). Handled here (inside the CORS middleware) so the browser can read it - an
    # unhandled 500 leaves without CORS headers and looks like "server unreachable".
    # Provider internals (status, body) stay in the server log.
    @app.exception_handler(LLMError)
    async def llm_unavailable(request: Request, exc: LLMError) -> JSONResponse:
        log.error("LLM request failed (status %s): %s", exc.status, str(exc)[:500])
        return JSONResponse(status_code=503, content={
            "detail": exc.user_message, "code": "llm_unavailable"})

    return app


app = create_app()
