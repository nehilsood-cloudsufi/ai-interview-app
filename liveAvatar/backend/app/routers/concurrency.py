from fastapi import APIRouter

from app.models import ConcurrencyResponse
from app.services.session_state import active_sessions

router = APIRouter()


@router.get("/api/concurrency", response_model=ConcurrencyResponse)
async def get_concurrency():
    return {"active_sessions": active_sessions.count}
