# Stage 1: Build the React frontend
FROM node:20-alpine as frontend-builder

WORKDIR /app/frontend

# Build-time Vite flags (see frontend/src/config.ts). Defaults preserve
# current behavior: self-view camera panel shown, sessions-sheet link hidden.
ARG VITE_SHOW_SELF_VIEW=true
ARG VITE_SESSIONS_SHEET_URL=""
ENV VITE_SHOW_SELF_VIEW=${VITE_SHOW_SELF_VIEW}
ENV VITE_SESSIONS_SHEET_URL=${VITE_SESSIONS_SHEET_URL}

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Stage 2: Build the Python backend and serve
FROM python:3.13-slim

WORKDIR /app

# Install uv for dependency management (project is uv-managed: pyproject.toml + uv.lock)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy backend code
COPY backend/ ./backend/
RUN cd backend && uv sync --frozen --no-dev

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Run as a non-root user with a real home directory (a homeless system user
# defaults to HOME=/nonexistent, which breaks uv's cache directory at runtime)
RUN addgroup --system app && adduser --system --ingroup app --home /home/app app \
    && chown -R app:app /app
USER app
ENV HOME=/home/app

# Expose the port Cloud Run expects
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/api/concurrency', timeout=3).status==200 else 1)"

# Command to run the application using uvicorn
WORKDIR /app/backend
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
