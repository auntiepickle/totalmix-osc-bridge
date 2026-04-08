FROM python:3.12-slim

# System deps for MIDI/ALSA
RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy code
COPY *.py ./
COPY web/ ./web/

# Web port from .env (single source of truth)
ARG WEB_PORT=8088
ENV WEB_PORT=${WEB_PORT}

EXPOSE ${WEB_PORT}

# Run with configurable port
CMD sh -c "uvicorn web.web_client:app --host 0.0.0.0 --port ${WEB_PORT} --log-level info"