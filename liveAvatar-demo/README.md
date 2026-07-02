# LiveAvatar Interview Demo

This directory contains a Proof of Concept (POC) for an AI Engineering Interview application using LiveAvatar's FULL Mode.

## Architecture

- **Backend (`/backend`)**: A Python FastAPI server managed with `uv`. It handles session token generation, stopping sessions safely to avoid concurrency issues, and processing resume uploads (PDF/DOCX/TXT) to dynamically inject context into the LLM.
- **Frontend (`/frontend`)**: A React application built with Vite and Tailwind CSS. It uses `@heygen/liveavatar-web-sdk` to render the interactive avatar and provides a professional dashboard layout, network quality monitoring, and a document upload interface.

## Getting Started

1. Set up your `.env` variables in both frontend and backend directories.
2. Ensure you have `uv` installed for the backend.
3. Run the backend setup script (`python setup.py`) to provision your Gemini LLM integration and base context on LiveAvatar.
4. Start both servers:
   - Backend: `uv run uvicorn server:app --port 3001 --reload`
   - Frontend: `npm run dev`

## Features

- **Full-Screen Dashboard UI:** Modern, split-screen layout separating document context from the live video feed.
- **Dynamic Context Injection:** Upload multiple files (resumes, portfolios) before starting; the backend parses them and generates a bespoke LiveAvatar context for the interview.
- **Voice Activity Detection (VAD) Visuals:** Animated audio bars provide feedback on speaking states (Listening, Thinking, Speaking).
- **Network Quality Indicator:** Real-time feedback on connection strength to the server.
- **Safe Session Cleanup:** Ensures active tokens are properly terminated upon closing the tab to prevent "Active session exists" errors.