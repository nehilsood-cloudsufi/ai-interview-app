"""FastAPI application entrypoint: wiring only, no request logic of its own.

Configures logging, builds the `app`, adds permissive CORS (the frontend and
backend are same-origin in production, but the wildcard keeps dev tunnels
simple), and includes every router: concurrency, interview, sessions,
transcripts, and the HeyGen llm_gateway. There is no agent worker process and
no startup provisioning - the conversation logic runs in-process inside these
routes, and the per-interview LiveAvatar resources are provisioned per session
in the sessions router.

In production this same container also serves the compiled React frontend:
when `frontend/dist` exists it is mounted at "/", and a middleware rewrites
any non-API/non-LLM 404 to `index.html` so client-side routes (like `/prod`)
load the SPA. That dist directory does not exist during CI, so the static
mount and fallback are effectively no-ops there.
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_config import configure_logging
from app.routers import concurrency, interview, llm_gateway, sessions, transcripts

configure_logging()
logger = logging.getLogger(__name__)

if not settings.liveavatar_api_key:
    logger.warning("LIVEAVATAR_API_KEY is missing from the environment variables. Users will need to provide their own.")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(concurrency.router)
app.include_router(interview.router)
app.include_router(sessions.router)
app.include_router(transcripts.router)
app.include_router(llm_gateway.router)

# Serve React Frontend in production
frontend_dist = os.path.join(os.path.dirname(__file__), "../../frontend/dist")


@app.middleware("http")
async def fallback_to_index(request: Request, call_next):
    """SPA history-fallback: if a request 404s and its path is not an API or
    LLM-gateway route, serve the frontend's `index.html` instead so
    client-side-routed URLs (e.g. `/prod`) resolve to the single-page app.
    Real backend 404s (paths under `/api/` or `/llm/`) are passed through
    unchanged."""
    response = await call_next(request)
    if response.status_code == 404 and not request.url.path.startswith(("/api/", "/llm/")):  # pragma: no cover
        # Unreachable in CI: frontend/dist doesn't exist until the frontend is built.
        return FileResponse(os.path.join(frontend_dist, "index.html"))
    return response


if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
