# AI Interview App (React + LiveAvatar) — Implementation Plan

## Background & Motivation
Build a browser-based AI interview app leveraging LiveKit Cloud, LiveKit Python Agent (STT → LLM → TTS), LiveAvatar plugin, and a React frontend. The application will conduct an automated 5-minute interview using a speaking avatar and save the final transcript.

## Architecture Stack
- **Frontend**: React (Vite), LiveKit Client, React Context API for state management.
- **Backend**: Python, FastAPI (Token generation), LiveKit Agents.
- **AI Pipeline**: 
  - **LLM**: Google Gemini (`livekit-plugins-google`)
  - **STT**: Deepgram (`livekit-plugins-deepgram`) - *Recommended for low latency*
  - **TTS**: ElevenLabs (`livekit-plugins-elevenlabs`) - *Recommended for realistic voices*
  - **Visual**: LiveAvatar (`livekit-plugins-liveavatar`)
- **Version Control**: Git, GitHub (via `gh` CLI).

---

## Phase 0: Repository & Git Initialization
**Goal:** Setup version control, create project scaffold, and push to GitHub.

- [ ] **TODO 0.1:** Initialize Git repository (`git init`).
- [ ] **TODO 0.2:** Create base directories: `backend/` and `frontend/`.
- [ ] **TODO 0.3:** Create project `README.md` and `.gitignore` (ignoring node_modules, venv, .env).
- [ ] **TODO 0.4:** Use GitHub CLI to create the remote repository: 
  - `gh repo create ai-interview-app --public --source=. --remote=origin`
- [ ] **Git Milestone 0:**
  - `git add .`
  - `git commit -m "chore: initial project structure and README"`
  - `git tag v0.1.0-setup`
  - `git push origin main --tags`

---

## Phase 1: Backend API & Environment Setup
**Goal:** Set up Python virtual environment and build the LiveKit token generation endpoint using FastAPI.

- [ ] **TODO 1.1:** Setup Python Virtual Environment
  - `cd backend` && `python3 -m venv venv`
  - Install base dependencies: `fastapi`, `uvicorn`, `livekit-server-sdk`, `livekit-agents`, `python-dotenv`.
  - Install AI plugins: `livekit-plugins-liveavatar`, `livekit-plugins-google`, `livekit-plugins-deepgram`, `livekit-plugins-elevenlabs`.
- [ ] **TODO 1.2:** Configure Environment Variables
  - Create `.env` file with `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEAVATAR_API_KEY`, `LIVEAVATAR_AVATAR_ID`, `GEMINI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`.
- [ ] **TODO 1.3:** Implement FastAPI Token Server (`server.py`)
  - Create a `GET /token` endpoint expecting `room` and `name` query parameters.
  - Generate and return a LiveKit `AccessToken` with `can_publish`, `can_subscribe`, and `can_publish_data` permissions.
  - Configure CORS middleware in FastAPI to allow requests from the React frontend (usually `localhost:5173`).
- [ ] **Tests for Phase 1:**
  - *Automated:* Add a simple test (e.g., via `pytest` and `TestClient`) for the `GET /token` route to ensure it returns a valid token string.
  - *Manual:* Run `uvicorn server:app --reload` and visit `http://localhost:8000/token?room=test&name=candidate` in the browser to verify the JSON response.
- [ ] **Git Milestone 1:**
  - `git add backend/`
  - `git commit -m "feat: backend setup and fastapi token generation"`
  - `git tag v0.2.0-backend-api`

---

## Phase 2: LiveKit Python Agent Implementation
**Goal:** Implement the conversational AI Agent integrating STT, Gemini LLM, TTS, and the LiveAvatar plugin.

- [ ] **TODO 2.1:** Define System Prompts (`prompts.py`)
  - Write a system prompt enforcing a professional interviewer persona. Ensure instructions explicitly state to ask *one question at a time* and wait for a response.
- [ ] **TODO 2.2:** Core Agent Logic (`agent.py`)
  - Define `AgentServer` and the entry point using `@server.rtc_session`.
  - Initialize the AI pipeline instances: `deepgram.STT()`, `google.LLM()`, and `elevenlabs.TTS()`.
  - Create the `AgentSession` passing in the AI instances.
- [ ] **TODO 2.3:** LiveAvatar Integration
  - Initialize `liveavatar.AvatarSession` using the avatar ID from the `.env` file.
  - Start the avatar session: `await avatar.start(session, room=ctx.room)`.
  - Start the agent session: `await session.start(room=ctx.room)`.
- [ ] **Tests for Phase 2:**
  - *Manual/E2E:* Run the agent server (`python agent.py`). Use the LiveKit Agent Sandbox/Console to connect to the room. Verify that the avatar joins, visualizes speech, and the Gemini LLM responds correctly to voice input.
- [ ] **Git Milestone 2:**
  - `git add backend/`
  - `git commit -m "feat: agent pipeline with gemini and liveavatar"`
  - `git tag v0.3.0-agent-core`

---

## Phase 3: React Frontend Scaffold & LiveKit Connection
**Goal:** Build the React UI using Vite, set up React Context, and establish the WebRTC connection.

- [ ] **TODO 3.1:** Initialize React App
  - `npm create vite@latest frontend -- --template react-ts`
  - Install LiveKit packages: `livekit-client`, `@livekit/components-react`, `@livekit/components-styles`.
  - Install router (optional but recommended): `react-router-dom`.
- [ ] **TODO 3.2:** State Management (`InterviewContext.tsx`)
  - Create a React Context to globally manage `roomName`, `participantName`, `token`, and `connectionState`.
- [ ] **TODO 3.3:** Implement Room Connection (`LiveKitRoom.tsx`)
  - Fetch the token from the FastAPI backend using `fetch()`.
  - Use the `<LiveKitRoom>` component with the fetched token and `LIVEKIT_URL`.
  - Map the remote Avatar video track to a full-screen or centered video component.
  - Implement a microphone toggle button and active speaking indicator.
- [ ] **Tests for Phase 3:**
  - *Manual:* Run the frontend (`npm run dev`) and backend simultaneously. Join a room from the UI and verify that 2-way audio works and the avatar video stream is displayed.
- [ ] **Git Milestone 3:**
  - `git add frontend/`
  - `git commit -m "feat: react frontend context and livekit connection"`
  - `git tag v0.4.0-frontend-base`

---

## Phase 4: Structured Interview Flow & Transcript Capture
**Goal:** Enforce multi-stage interview logic and save conversation data.

- [ ] **TODO 4.1:** Agent State Machine (Backend)
  - Update `agent.py` to maintain interview state (Greeting -> Background -> Technical -> Behavioral -> Closing).
  - Conditionally inject contextual prompts based on the current stage.
- [ ] **TODO 4.2:** Complete UI Flow (Frontend)
  - **Landing Screen:** Add inputs for Candidate Name and Job Role.
  - **Interview Screen:** Display the Avatar, a "Live Transcription" overlay using LiveKit's transcription events, and an "End Interview" button.
  - **Completion Screen:** Display a summary and a thank you message.
- [ ] **TODO 4.3:** Transcript Capture
  - In `agent.py`, subscribe to the LLM/STT transcription events.
  - Upon room disconnection or interview completion, dump the aggregated conversation array into `backend/interviews/<timestamp>-<name>.json`.
- [ ] **Tests for Phase 4:**
  - *Automated (Backend):* Write unit tests for the prompt generation logic to ensure it returns the correct string based on the stage enum.
  - *E2E Test:* Complete a full 5-minute interview. Verify the UI transitions from Landing -> Interview -> End, and verify the JSON file is correctly populated in the `backend/interviews/` directory.
- [ ] **Git Milestone 4:**
  - `git commit -am "feat: structured interview flow, ui polish, and transcripts"`
  - `git tag v0.5.0-interview-flow`

---

## Phase 5: Stability, Error Handling & Deployment
**Goal:** Harden the application against edge cases and prepare for production.

- [ ] **TODO 5.1:** Frontend Error Handling
  - Detect if microphone permission is denied and display an actionable error state.
  - Implement loading spinners during LiveKit Room connection phases.
  - Handle unexpected room disconnects by routing the user to a fallback/error screen.
- [ ] **TODO 5.2:** Backend Error Handling
  - Wrap agent logic in global try/except blocks to prevent hard crashes.
  - Implement graceful shutdown logic handling `SIGINT`/`SIGTERM` to clean up active LiveKit and LiveAvatar sessions.
- [ ] **TODO 5.3:** Deployment Preparation
  - Ensure all environment variables are documented in a `.env.example` file.
  - Provide a production build script for Vite (`npm run build`).
- [ ] **Tests for Phase 5:**
  - *Manual Fault Injection:* 
    1. Revoke mic permissions in the browser and verify the UI response. 
    2. Abruptly kill the FastAPI backend and ensure the React frontend handles the dropped WebSocket gracefully.
- [ ] **Git Milestone 5:**
  - `git commit -am "chore: error handling, stability improvements, and deployment prep"`
  - `git tag v1.0.0-production-ready`
  - `git push origin main --tags`