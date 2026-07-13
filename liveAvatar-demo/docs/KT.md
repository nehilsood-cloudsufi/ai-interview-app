# LiveAvatar AI Engineering Interview Application - Comprehensive Knowledge Transfer (KT)

Welcome to the Knowledge Transfer document for the LiveAvatar AI Interview POC! If you're an intern getting up to speed, a manager reviewing the architecture, or a senior engineer preparing to extend the system, this guide will walk you through everything you need to know. 

We'll treat this like a guided tour. We won't just look at *what* the code does; we'll explain *why* it's built this way.

---

## 1. The Big Picture: What Are We Building?

Imagine a candidate sitting down for a technical interview, but instead of a human engineer, they are greeted by an AI avatar. This avatar can see their resume, ask them questions about AI Engineering (like RAG or LLMs), and respond in real-time with perfectly lip-synced video and audio. 

**Our Goal:** To build a robust, scalable web application that conducts this interview using HeyGen's LiveAvatar technology, while ensuring the system is secure and cost-effective.

---

## 2. The Tech Stack (and Why We Chose It)

To make this happen seamlessly, we divided the application into two main parts: a fast, modern frontend for the user, and a secure backend to talk to the AI services.

### The Frontend (What the User Sees)
- **React 19 & TypeScript**: We chose React for its component-based architecture, making it easy to manage complex UI states (like whether the avatar is listening or speaking). TypeScript ensures we catch errors early.
- **Vite**: A lightning-fast build tool that replaces older tools like Webpack. It makes local development incredibly quick.
- **Tailwind CSS v4 & Lucide React**: Tailwind allows us to rapidly style the application without leaving our HTML, and Lucide provides clean, modern icons.
- **`@heygen/liveavatar-web-sdk`**: This is the secret sauce. It handles the extremely complex WebRTC (real-time communication) connection between the user's browser and HeyGen's video streaming servers. We are using **FULL Mode**, which means HeyGen's servers handle the LLM (AI brain) connection for us.

### The Backend (The Secure Middleman)
- **Python 3.11 & FastAPI**: Python is the standard for AI applications. FastAPI is incredibly fast and easy to use for building APIs. 
- **`uv`**: A modern, extremely fast Python package manager that replaces `pip` and `virtualenv`.
- **`pymupdf` & `python-docx`**: These libraries allow us to read the text inside PDFs and Word documents that the user uploads.

### Why do we even need a backend?
You might wonder, *"Why can't the React app just talk to LiveAvatar directly?"*
**Security.** To start a LiveAvatar session, you need an API key. If we put that key in the React code, anyone could view the source code in their browser, steal the key, and rack up a massive bill on our account. The backend acts as a **Proxy**. The React app asks the backend to start a session, the backend securely uses the API key to get a temporary `session_token`, and gives that token to React.

---

## 3. Step-by-Step Architecture Walkthrough

Let's follow the data flow when a user actually uses the app.

1. **Loading the App**: The user navigates to our Cloud Run URL. The FastAPI backend receives the request and, because it's configured to serve static files, it sends the compiled React app (`index.html`, JavaScript, CSS) to the browser.
2. **Uploading Context**: The candidate uploads their resume (up to 5 files). The React app sends these files to the backend via the `/api/upload-resume` endpoint.
3. **Processing Context**: The backend uses `pymupdf` or `python-docx` to extract the text. It combines this text with a base system prompt ("You are an AI interviewer..."). It then securely calls LiveAvatar's API (`POST /v1/contexts`) to save this specific candidate's context. LiveAvatar replies with a unique `context_id`.
4. **Starting the Session**: The React app now calls the backend's `/api/session` endpoint, passing along the `context_id`. 
5. **Token Generation**: The backend uses our secure `LIVEAVATAR_API_KEY` to ask LiveAvatar for a `session_token`. We hardcode `is_sandbox: True` here to save money during testing, and we use a specific Sandbox Avatar ID (`dd73ea75...`).
6. **WebRTC Connection**: The backend gives the `session_token` back to React. React passes it to the `LiveAvatarSession` SDK. The SDK reaches out to LiveKit (the underlying infrastructure) and establishes a direct video/audio peer-to-peer connection. The avatar appears on screen!

---

## 4. Deep Dive: Frontend Mechanics (`App.tsx`)

If you look inside `/liveAvatar-demo/frontend/src/App.tsx`, here are the key concepts you need to understand:

- **Environment Variables & Fallbacks**: During deployment, we intentionally exclude the local `.env` file for security. To prevent the app from breaking when it can't find `import.meta.env.VITE_CONTEXT_ID`, we've added hardcoded fallback IDs in the code.
- **Event Listeners (VAD)**: Voice Activity Detection (VAD) is how the system knows who is talking. The `LiveAvatarSession` object emits events like `AgentEventsEnum.AVATAR_SPEAK_STARTED` and `USER_SPEAK_STARTED`. We listen to these events to change our React state (`speakingState`), which animates the little audio bars on the screen.
- **The "Before Unload" Safety Net**: LiveAvatar only allows a certain number of concurrent sessions. If a user just closes their browser tab, the session might stay alive for a few minutes on the server, blocking others. We use the browser's `beforeunload` event with `keepalive: true` to send a final `/api/session/stop` request as the tab dies, ensuring clean cleanup.

---

## 5. Deep Dive: Backend Mechanics (`server.py`)

Inside `/liveAvatar-demo/backend/server.py`, the logic is designed for resilience:

- **Concurrency Tracking**: We maintain a simple `active_sessions_count` variable. This helps us monitor if we are hitting LiveKit's concurrency limits.
- **Garbage Collection (Contexts)**: When a session stops (in `/api/session/stop`), we don't just stop the video. We also send a `DELETE` request to LiveAvatar to destroy the `context_id` we created for that specific resume. If we didn't do this, our LiveAvatar workspace would eventually fill up with thousands of temporary resumes.
- **FastAPI Static Serving**: Notice the `fallback_to_index` middleware. This tells FastAPI: "If someone asks for a URL that isn't an API route (like `/`), send them the React `index.html`." This is how we achieve a Single Unified Container.

---

## 6. Deployment Strategy (Google Cloud Run)

We deploy both the frontend and backend together as a single container on Google Cloud Run. This is much cheaper and simpler to manage than deploying them separately.

- **The Dockerfile (Multi-Stage)**:
  1. **Stage 1 (Node.js)**: It installs `npm` packages, builds the React app into optimized static files (inside `/dist`), and then discards all the heavy Node.js source files.
  2. **Stage 2 (Python)**: It starts fresh with a lightweight Python image. It installs FastAPI, copies the Python backend code, and finally copies just the compiled `/dist` folder from Stage 1. It then starts `uvicorn` on port 8080.
- **The `.gcloudignore` File**: This is critical. It tells Google Cloud "Do NOT upload my local `node_modules` or `.venv` folders to the cloud builder." If you upload a Mac's `.venv` folder to a Linux cloud builder, the build will fail because the binaries are incompatible.
- **Secret Manager**: The `deploy_setup.sh` script automates creating a secure vault (Google Cloud Secret Manager) for our `LIVEAVATAR_API_KEY` and giving Cloud Run permission to read it. When the container boots, Cloud Run injects the secret securely as an environment variable.

---

## 7. Troubleshooting & FAQ

**Q: The avatar connects but is completely silent and ignores speech. What's wrong?**
**A:** This is almost always a missing `context_id`. In FULL Mode, if LiveAvatar doesn't know *what* its personality is (the context), it goes into a restricted mode where it streams video but ignores input. Check that the `.env` variables or the fallback IDs in `App.tsx` are correctly being passed in the `/api/session` payload.

**Q: I'm getting an "Active session already exists" error.**
**A:** A previous session didn't close cleanly (e.g., your browser crashed, bypassing the `beforeunload` event). The system is protecting you from starting a second concurrent session. You can either wait 5 minutes for the idle timeout, or manually trigger the `/api/session/stop` endpoint.

**Q: Cloud Build is failing with an error about missing modules or incompatible binaries.**
**A:** You likely accidentally uploaded your local environments. Ensure `.gcloudignore` is present in the root of the project and contains `node_modules`, `.venv`, and `.env`.

**Q: Why is `is_sandbox: True` hardcoded?**
**A:** We are currently using the default Sandbox Avatar ID. If you try to use a sandbox avatar with `is_sandbox: False`, LiveKit will inexplicably time out and the connection will fail. When we move to production with a custom avatar, this flag must be changed.