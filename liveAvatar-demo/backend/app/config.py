import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    liveavatar_api_key: str | None = field(default_factory=lambda: os.getenv("LIVEAVATAR_API_KEY"))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    liveavatar_base_url: str = "https://api.liveavatar.com/v1"
    avatar_id: str = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"
    max_files: int = 5
    max_file_size_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 10
    interview_base_prompt: str = (
        "You are an experienced technical interviewer assessing a candidate for an "
        "AI Engineering role. Ask them a few simple, basic questions about RAG "
        "(Retrieval-Augmented Generation), fundamentals of Large Language Models (LLMs), "
        "and general Generative AI basics. Keep your responses concise and conversational. "
        "Do not output markdown, speak naturally."
    )


settings = Settings()
