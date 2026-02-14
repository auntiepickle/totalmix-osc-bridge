FROM python:3.12-slim

RUN apt-get update && apt-get install -y libasound2-dev && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir paho-mqtt

WORKDIR /app

# Copy ALL Python files
COPY *.py ./

CMD ["python3", "-u", "bridge.py"]