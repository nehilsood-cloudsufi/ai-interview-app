import os
import io
import httpx
import pymupdf
import docx
import uuid
from typing import List
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LIVEAVATAR_API_KEY = os.getenv("LIVEAVATAR_API_KEY")
if not LIVEAVATAR_API_KEY:
    raise RuntimeError("LIVEAVATAR_API_KEY is missing from the environment variables.")
    
LIVEAVATAR_BASE_URL = "https://api.liveavatar.com/v1"

@app.post("/api/upload-resume")
async def upload_resume(files: List[UploadFile] = File(...)):
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 files allowed")
    
    extracted_text = ""
    for file in files:
        contents = await file.read()
        if len(contents) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 5MB limit")
        
        filename = file.filename.lower()
        try:
            if filename.endswith('.pdf'):
                doc = pymupdf.open(stream=contents, filetype="pdf")
                if len(doc) > 10:
                    raise HTTPException(status_code=400, detail=f"File {file.filename} exceeds 10 page limit")
                for page in doc:
                    extracted_text += page.get_text() + "\n"
            elif filename.endswith('.docx'):
                doc = docx.Document(io.BytesIO(contents))
                for para in doc.paragraphs:
                    extracted_text += para.text + "\n"
            elif filename.endswith('.txt'):
                extracted_text += contents.decode('utf-8', errors='ignore') + "\n"
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported file format: {file.filename}. Only PDF, DOCX, and TXT allowed.")
        except Exception as e:
            print(f"Error parsing file {filename}: {e}")
            raise HTTPException(status_code=400, detail=f"Failed to read {file.filename}")
            
    base_prompt = "You are an experienced technical interviewer assessing a candidate for an AI Engineering role. Ask them a few simple, basic questions about RAG (Retrieval-Augmented Generation), fundamentals of Large Language Models (LLMs), and general Generative AI basics. Keep your responses concise and conversational. Do not output markdown, speak naturally."
    full_prompt = f"{base_prompt}\n\nCandidate's Additional Context (Resume/Portfolio):\n{extracted_text}"
    
    if not LIVEAVATAR_API_KEY:
        raise HTTPException(status_code=500, detail="LIVEAVATAR_API_KEY missing")
        
    async with httpx.AsyncClient() as client:
        try:
            unique_name = f"AI Interviewer w/ Context {uuid.uuid4().hex[:8]}"
            context_res = await client.post(
                f"{LIVEAVATAR_BASE_URL}/contexts",
                json={
                    "name": unique_name,
                    "prompt": full_prompt[:25000], # Keep within reasonable limits
                    "opening_text": "Hello! I've reviewed the documents you shared. Let me know when you're ready to begin the technical interview."
                },
                headers={"X-API-KEY": LIVEAVATAR_API_KEY}
            )
            context_res.raise_for_status()
            context_id = context_res.json()["data"]["id"]
            return {"context_id": context_id}
        except httpx.HTTPStatusError as e:
            print(f"LiveAvatar Context Error: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail="Failed to create context")

@app.post("/api/session")
async def create_session(request: Request):
    if not LIVEAVATAR_API_KEY:
        raise HTTPException(status_code=500, detail="LIVEAVATAR_API_KEY not configured on backend")
        
    try:
        body = await request.json()
        context_id = body.get("context_id")
        llm_configuration_id = body.get("llm_configuration_id")
        avatar_id = body.get("avatar_id")
        
        # 1. Generate Session Token
        token_payload = {
            "mode": "FULL",
            "avatar_id": "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a",
            "is_sandbox": True,
            "avatar_persona": {
                "language": "en"
            }
        }
        
        if llm_configuration_id:
            token_payload["llm_configuration_id"] = llm_configuration_id
            
        if context_id:
            token_payload["avatar_persona"]["context_id"] = context_id

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                f"{LIVEAVATAR_BASE_URL}/sessions/token",
                json=token_payload,
                headers={
                    "X-API-KEY": LIVEAVATAR_API_KEY,
                    "Content-Type": "application/json"
                }
            )
            
            token_response.raise_for_status()
            token_data = token_response.json()["data"]
            session_token = token_data["session_token"]
            session_id = token_data["session_id"]
            
            return {
                "session_token": session_token,
                "session_id": session_id
            }
            
    except httpx.HTTPStatusError as e:
        print(f"LiveAvatar API Error: {e.response.text}")
        raise HTTPException(status_code=e.response.status_code, detail="Failed to create or start session")
    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.post("/api/session/stop")
async def stop_session(request: Request):
    try:
        body = await request.json()
        session_token = body.get("session_token")
        context_id = body.get("context_id") # Get context_id to clean up
        
        if not session_token:
            return {"status": "ignored"}
            
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{LIVEAVATAR_BASE_URL}/sessions/stop",
                headers={"Authorization": f"Bearer {session_token}"}
            )
            
            # Clean up the dynamically created context if one was used
            if context_id:
                try:
                    await client.delete(
                        f"{LIVEAVATAR_BASE_URL}/contexts/{context_id}",
                        headers={"X-API-KEY": LIVEAVATAR_API_KEY}
                    )
                except Exception as e:
                    print(f"Failed to clean up context {context_id}: {e}")
                    
            return {"status": "stopped", "api_status": res.status_code}
    except Exception as e:
        print(f"Error stopping session: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to stop session")

# Serve React Frontend in production
frontend_dist = os.path.join(os.path.dirname(__file__), "../frontend/dist")

@app.middleware("http")
async def fallback_to_index(request: Request, call_next):
    response = await call_next(request)
    if response.status_code == 404 and not request.url.path.startswith("/api/"):
        return FileResponse(os.path.join(frontend_dist, "index.html"))
    return response

if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

