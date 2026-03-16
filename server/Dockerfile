FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    xvfb \
    xdotool \
    scrot \
    curl \
    zsh \
    chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV DISPLAY=:99
ENV PYTHONUNBUFFERED=1
ENV GOOGLE_GENAI_USE_VERTEXAI=true
ENV GOOGLE_CLOUD_LOCATION=us-central1

EXPOSE 8080

CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset & uvicorn main:app --host 0.0.0.0 --port 8080"]
