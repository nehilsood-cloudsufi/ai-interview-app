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

## Deployment

The application is configured for a Single Unified Cloud Run Service deployment. The FastAPI backend serves the compiled React static files on Port 8080 to avoid CORS issues.

- **Docker:** A multi-stage `Dockerfile` handles building the Node/React frontend and the Python backend.
- **Google Cloud Run:** Use `gcloud run deploy` to deploy. The `.gcloudignore` file ensures local `.env`, `node_modules`, and `.venv` are not uploaded to Cloud Build.
- **Secrets Management:** The `deploy_setup.sh` script automates the creation of a Google Cloud Secret Manager secret (`LIVEAVATAR_API_KEY`) and binds the necessary IAM policies. This secret is injected into the Cloud Run container at runtime.

## Features

- **Full-Screen Dashboard UI:** Modern, split-screen layout separating document context from the live video feed.
- **Dynamic Context Injection:** Upload multiple files (resumes, portfolios) before starting; the backend parses them and generates a bespoke LiveAvatar context for the interview.
- **Voice Activity Detection (VAD) Visuals:** Animated audio bars provide feedback on speaking states (Listening, Thinking, Speaking).
- **Network Quality Indicator:** Real-time feedback on connection strength to the server.
- **Safe Session Cleanup:** Ensures active tokens are properly terminated upon closing the tab to prevent "Active session exists" errors.