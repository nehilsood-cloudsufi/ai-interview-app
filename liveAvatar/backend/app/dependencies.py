from app.config import settings


def resolve_api_key(request_api_key: str | None) -> str | None:
    return request_api_key if request_api_key else settings.liveavatar_api_key
