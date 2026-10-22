FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 \
    libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 \
    libpango-1.0-0 libcairo2 \
    libx11-xcb1 libxcb1 libxext6 \
    libxi6 libxtst6 \
    fonts-liberation fonts-noto-color-emoji fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium

COPY . .

RUN mkdir -p output data

EXPOSE 8000

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
