import sys

import httpx

from app.config import settings


def main():
    if not settings.liveavatar_api_key or not settings.gemini_api_key:
        print("Error: Missing LIVEAVATAR_API_KEY or GEMINI_API_KEY in .env")
        sys.exit(1)

    print("1. Storing Gemini API Key securely in LiveAvatar...")
    try:
        with httpx.Client() as client:
            # Create Secret
            secret_res = client.post(
                f"{settings.liveavatar_base_url}/secrets",
                json={
                    "secret_type": "GEMINI_API_KEY",
                    "secret_value": settings.gemini_api_key,
                    "secret_name": "Gemini API Key Python",
                },
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            secret_res.raise_for_status()
            secret_id = secret_res.json()["data"]["id"]
            print(f"✅ Secret created: {secret_id}")

            # Create LLM Configuration
            print("2. Creating LLM Configuration pointing to Gemini OpenAI-compatible endpoint...")
            llm_res = client.post(
                f"{settings.liveavatar_base_url}/llm-configurations",
                json={
                    "display_name": "Gemini 3.5 Flash",
                    "model_name": "gemini-3.5-flash",
                    "secret_id": secret_id,
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                },
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            llm_res.raise_for_status()
            llm_config_id = llm_res.json()["data"]["id"]
            print(f"✅ LLM Configuration created: {llm_config_id}")

            # Create Context
            print("3. Creating Interviewer Context...")
            context_res = client.post(
                f"{settings.liveavatar_base_url}/contexts",
                json={
                    "name": "AI Engineering Interviewer",
                    "prompt": "You are an experienced technical interviewer assessing a candidate for an AI Engineering role. Ask them a few simple, basic questions about RAG (Retrieval-Augmented Generation), fundamentals of Large Language Models (LLMs), and general Generative AI basics. Keep your responses concise and conversational. Do not output markdown, speak naturally.",
                    "opening_text": "Hello! Thank you for taking the time to speak with me today. Let me know when you're ready to begin the technical interview.",
                },
                headers={"X-API-KEY": settings.liveavatar_api_key},
            )
            context_res.raise_for_status()
            context_id = context_res.json()["data"]["id"]
            print(f"✅ Context created: {context_id}")

            print("\n========================================")
            print("SETUP COMPLETE! Save these IDs for your frontend environment:")
            print(f"VITE_LLM_CONFIG_ID={llm_config_id}")
            print(f"VITE_CONTEXT_ID={context_id}")
            print("========================================\n")

    except httpx.HTTPStatusError as e:
        print(f"Setup failed: {e.response.text}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")


if __name__ == "__main__":
    main()
