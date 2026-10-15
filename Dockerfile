FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libredwg-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt app.py ./
RUN pip install --no-cache-dir -r requirements.txt
CMD gunicorn -b 0.0.0.0:${PORT:-5000} app:app
