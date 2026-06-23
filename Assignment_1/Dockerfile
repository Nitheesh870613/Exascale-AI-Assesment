FROM python:3.11-slim AS trainer

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data/ ./data/
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Train the model at build time
RUN python backend/train_model.py

# ── Runtime image ──────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app
COPY --from=trainer /app/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=trainer /app/backend/ ./backend/
COPY --from=trainer /app/data/ ./data/
COPY --from=trainer /app/frontend/ ./frontend/

# Copy trained model artifact
COPY --from=trainer /app/backend/model_artifact.pkl ./backend/model_artifact.pkl

# Serve static frontend
RUN pip install --no-cache-dir aiofiles

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
