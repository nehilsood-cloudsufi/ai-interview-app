import logging
import uuid

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_gemini_secret_id: str | None = None
_gemini_llm_configuration_id: str | None = None


def get_gemini_llm_configuration_id() -> str | None:
    return _gemini_llm_configuration_id


async def provision_gemini() -> None:
    global _gemini_secret_id, _gemini_llm_configuration_id

    if not (settings.gemini_api_key and settings.liveavatar_api_key):
        return

    logger.info("GEMINI_API_KEY detected. Attempting to auto-provision Gemini LLM Configuration...")
    async with httpx.AsyncClient() as client:
        try:
            # 1. Store API Key as Secret
            secret_res = await client.post(
                f"{settings.liveavatar_base_url}/secrets",
                json={
                    "secret_type": "GEMINI_API_KEY",
                    "secret_value": settings.gemini_api_key,
                    "secret_name": f"Gemini API Key Auto {uuid.uuid4().hex[:8]}",
                },
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            secret_res.raise_for_status()
            secret_json = secret_res.json()
            _gemini_secret_id = secret_json["data"]["id"]
            logger.info("Created Secret: %s", _gemini_secret_id)

            # 2. Create LLM Configuration
            llm_res = await client.post(
                f"{settings.liveavatar_base_url}/llm-configurations",
                json={
                    "display_name": "Gemini 3.5 Flash",
                    "model_name": "gemini-3.5-flash",
                    "secret_id": _gemini_secret_id,
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                },
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            llm_res.raise_for_status()
            llm_json = llm_res.json()
            # Use "id" or fallback if "llm_configuration_id" isn't present
            _gemini_llm_configuration_id = llm_json["data"].get("id") or llm_json["data"].get(
                "llm_configuration_id"
            )
            logger.info("Created LLM Configuration: %s", _gemini_llm_configuration_id)

        except httpx.HTTPStatusError as e:
            logger.warning("LiveAvatar HTTP Error during auto-provisioning: %s", e.response.text)
            _gemini_secret_id = None
            _gemini_llm_configuration_id = None
        except Exception as e:
            logger.warning("Failed to auto-provision Gemini. Falling back to HeyGen AI. Error: %s", e)
            _gemini_secret_id = None
            _gemini_llm_configuration_id = None


async def deprovision_gemini() -> None:
    if not settings.liveavatar_api_key:
        return

    async with httpx.AsyncClient() as client:
        if _gemini_llm_configuration_id:
            try:
                logger.info("Cleaning up LLM config %s...", _gemini_llm_configuration_id)
                await client.delete(
                    f"{settings.liveavatar_base_url}/llm-configurations/{_gemini_llm_configuration_id}",
                    headers={"X-API-KEY": settings.liveavatar_api_key},
                )
            except Exception as e:
                logger.warning("Failed to delete LLM config: %s", e)
        if _gemini_secret_id:
            try:
                logger.info("Cleaning up Secret %s...", _gemini_secret_id)
                await client.delete(
                    f"{settings.liveavatar_base_url}/secrets/{_gemini_secret_id}",
                    headers={"X-API-KEY": settings.liveavatar_api_key},
                )
            except Exception as e:
                logger.warning("Failed to delete Secret: %s", e)
