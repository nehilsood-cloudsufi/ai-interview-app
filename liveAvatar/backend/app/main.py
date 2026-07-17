import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.logging_config import configure_logging
from app.routers import concurrency, resume, sessions, spike_llm_gateway, transcripts, vendor
from app.services import gemini_provisioning

configure_logging()
logger = logging.getLogger(__name__)

if not settings.liveavatar_api_key:
    logger.warning("LIVEAVATAR_API_KEY is missing from the environment variables. Users will need to provide their own.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await gemini_provisioning.provision_gemini()
    yield  # Let the app run
    await gemini_provisioning.deprovision_gemini()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(concurrency.router)
app.include_router(resume.router)
app.include_router(sessions.router)
app.include_router(transcripts.router)
app.include_router(vendor.router)
# SPIKE — Phase 0 of the Resonance plan. Delete once docs/llm-gateway-notes.md
# is written and Task B2 replaces it with the real gateway route.
app.include_router(spike_llm_gateway.router)

# Serve React Frontend in production
frontend_dist = os.path.join(os.path.dirname(__file__), "../../frontend/dist")


@app.middleware("http")
async def fallback_to_index(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == 404 and not request.url.path.startswith("/api/"):  # pragma: no cover
        # Unreachable in CI: frontend/dist doesn't exist until the frontend is built.
        return FileResponse(os.path.join(frontend_dist, "index.html"))
    return response


if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
