FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-ipafont-gothic \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "gunicorn 'web_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 4 --timeout 120 --max-requests 500 --max-requests-jitter 50 --access-logfile - --error-logfile -"]
