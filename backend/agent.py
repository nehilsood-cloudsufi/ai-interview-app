import os
import json
import time
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
    
    logger.info(f"connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    try:
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
        )

        agent.start(ctx.room, avatar)
        await avatar.start(agent, room=ctx.room)

        # State machine logic
        turn_count = 0
        stages = ["GREETING", "BACKGROUND", "TECHNICAL", "BEHAVIORAL", "CLOSING"]
        current_stage_idx = 0

        @agent.on("agent_speech_committed")
        def on_agent_speech_committed(msg: llm.ChatMessage):
            nonlocal turn_count, current_stage_idx
            turn_count += 1
            
            # Advance stage every 2 turns (1 main question + 1 follow-up)
            if turn_count % 2 == 0 and current_stage_idx < len(stages) - 1:
                current_stage_idx += 1
                stage_name = stages[current_stage_idx]
                logger.info(f"Transitioning to stage: {stage_name}")
                agent.chat_ctx.append(
                    role="system",
                    text=f"[SYSTEM INSTRUCTION] You are now moving to the {stage_name} stage of the interview. Adjust your next question accordingly."
                )
                
        @ctx.room.on("disconnected")
        def on_disconnected(*args):
            logger.info("Room disconnected, saving transcript...")
            os.makedirs("interviews", exist_ok=True)
            transcript = []
            for msg in agent.chat_ctx.messages:
                content = msg.content
                if isinstance(content, list):
                    content = " ".join([c for c in content if isinstance(c, str)])
                
                if msg.role != "system":
                    transcript.append({
                        "role": msg.role,
                        "content": str(content)
                    })
                    
            timestamp = int(time.time())
            filename = f"interviews/{timestamp}-transcript.json"
            with open(filename, "w") as f:
                json.dump(transcript, f, indent=2)
            logger.info(f"Transcript saved to {filename}")

        # Initial greeting
        await agent.say("Hello, welcome to your interview. Can you hear me clearly?", allow_interruptions=True)

    except Exception as e:
        logger.error(f"Error during agent execution: {e}")
        await ctx.room.disconnect()

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
