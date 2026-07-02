import os
import logging
from dotenv import load_dotenv

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    cli,
    llm,
    metrics,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, elevenlabs, google, liveavatar, silero

from prompts import SYSTEM_PROMPT

load_dotenv()
logger = logging.getLogger("ai-interview-agent")

def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    # Retrieve required environment variables
    liveavatar_api_key = os.getenv("LIVEAVATAR_API_KEY")
    liveavatar_id = os.getenv("LIVEAVATAR_AVATAR_ID")

    if not liveavatar_api_key or not liveavatar_id:
        logger.warning("LiveAvatar credentials not fully set. Avatar might not start.")

    initial_ctx = llm.ChatContext().append(
        role="system",
        text=SYSTEM_PROMPT,
    )
    
    # Initialize the STT, LLM, and TTS plugins
    # Using Deepgram for STT, Google Gemini for LLM, ElevenLabs for TTS
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Note: Using a placeholder or fallback for VAD if silero is not installed
    # Here we are relying on deepgram's STT and VAD capabilities implicitly
    
    agent = VoicePipelineAgent(
        vad=ctx.proc.userdata.get("vad"),
        stt=deepgram.STT(),
        llm=google.LLM(),
        tts=elevenlabs.TTS(),
        chat_ctx=initial_ctx,
    )
    
    # Initialize LiveAvatar Session
    avatar = liveavatar.AvatarSession(
        avatar_id=liveavatar_id,
        video_quality="high",
        # Using the standard API key config, assuming it's picked up by the plugin or explicitly passed if needed
    )

    agent.start(ctx.room, avatar)
    await avatar.start(agent, room=ctx.room)

    # Initial greeting
    await agent.say("Hello, welcome to your interview. Can you hear me clearly?", allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
