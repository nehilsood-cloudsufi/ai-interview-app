from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    context_id: str | None = None
    llm_configuration_id: str | None = None
    avatar_id: str | None = None
    api_key: str | None = None


class CreateSessionResponse(BaseModel):
    session_token: str
    session_id: str


class StopSessionRequest(BaseModel):
    session_token: str | None = None
    context_id: str | None = None
    api_key: str | None = None


class StopSessionResponse(BaseModel):
    status: str
    api_status: int | None = None


class UploadResumeResponse(BaseModel):
    context_id: str


class ConcurrencyResponse(BaseModel):
    active_sessions: int
