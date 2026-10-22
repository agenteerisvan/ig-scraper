FROM python:3.12-slim

WORKDIR /app

# Libs minimas pro httpx/ssl
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p output data

EXPOSE 8000

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
