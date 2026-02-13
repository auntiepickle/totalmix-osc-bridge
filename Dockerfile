FROM python:3.12-slim

RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir paho-mqtt

WORKDIR /app
COPY bridge.py .

# ← THIS IS THE IMPORTANT LINE
CMD ["python3", "-u", "bridge.py"]