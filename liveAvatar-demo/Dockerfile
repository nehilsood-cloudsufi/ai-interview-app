# Stage 1: Build the React frontend
FROM node:20-alpine as frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# Stage 2: Build the Python backend and serve
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if required (e.g., for pymupdf or docx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir uvicorn

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Expose the port Cloud Run expects
EXPOSE 8080

# Command to run the application using uvicorn
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8080"]
