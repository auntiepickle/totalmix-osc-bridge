FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    paho-mqtt==2.1.0 \
    python-osc==1.8.1

COPY bridge.py .

CMD ["python", "bridge.py"]
