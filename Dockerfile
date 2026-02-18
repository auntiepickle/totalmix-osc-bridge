FROM python:3.12-slim

# System deps (kept for future MIDI/ALSA support on RME UFX II + Cirklon)
RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements FIRST (Docker cache optimization)
COPY requirements.txt .

# Install Python packages from requirements (clean, reproducible)
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy ALL Python files
COPY *.py ./

CMD ["python3", "-u", "bridge.py"]