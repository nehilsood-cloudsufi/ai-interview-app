FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
# Build the React app. import.meta.env.PROD will be true, so API requests will use relative paths.
RUN npm run build

FROM python:3.11-slim
WORKDIR /app

# Install backend dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend assets to the expected location
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Expose port for Cloud Run
EXPOSE 8080

WORKDIR /app/backend
# Start Uvicorn bound to 0.0.0.0 and the port provided by Cloud Run
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]