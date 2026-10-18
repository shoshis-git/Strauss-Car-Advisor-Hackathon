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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.graph import AdvisorAgent
from .agent.llm import LLMClient, load_env_file, make_llm
from .agent.state import StateStore
from .api.routes import SessionLocks, router
from .data_loader import paths
from .data_loader.config_loader import get_config
from .data_loader.tax_loader import get_tax_db

log = logging.getLogger("strauss")

# Any localhost / 127.0.0.1 port (Vite 5173, CRA 3000, ...); more via CORS_ORIGINS=a,b
LOCAL_ORIGINS_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


def create_app(db_path: Optional[Path] = None, llm: Optional[LLMClient] = None) -> FastAPI:
    """App factory; tests inject a temporary database and a deterministic LLM."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_env_file()
        config = get_config()      # rules.json + challenge_data.xlsx + car_aliases.json
        tax_db = get_tax_db()      # builds/validates the indexed Tax Authority table
        store = StateStore(db_path or paths.APP_DB)
        agent = AdvisorAgent(config, store, llm or make_llm(config))
        app.state.config, app.state.tax_db, app.state.agent = config, tax_db, agent
        app.state.session_locks = SessionLocks()
        log.info("loaded rules %s: %d cars, %d occupations, %d tax records, LLM=%s",
                 config.rules.version, len(config.cars), len(config.occupations),
                 tax_db.count(), type(agent.llm).__name__)
        yield

    app = FastAPI(title="יועץ הרכב של שטראוס", version="1.0", lifespan=lifespan)
    extra = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=extra,
        allow_origin_regex=LOCAL_ORIGINS_REGEX,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
