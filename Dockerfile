FROM python:3.12-slim

WORKDIR /app

# Build tools para extensoes nativas (cryptg, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Teste: se nao importa aqui, o build falha com o erro visivel
RUN python -c "from instagrapi import Client; print('instagrapi OK')"

# Remove build tools pra imagem ficar leve (mantem libs runtime)
RUN apt-get purge -y gcc python3-dev && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p output data

EXPOSE 8000

CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000"]
