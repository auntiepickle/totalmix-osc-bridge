FROM python:3.12-slim

# System deps (kept for MIDI/ALSA + RME UFX II + Cirklon)
RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements FIRST (Docker cache optimization)
COPY requirements.txt .

# Install Python packages
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy ALL Python files + new web/ folder
COPY *.py ./
COPY web/ ./web/

# Expose web port
EXPOSE 8080

# Run the web client (which starts bridge in background + FastAPI)
CMD ["uvicorn", "web.web_client:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]