FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias del sistema mínimas (ccxt necesita openssl/curl, ya en slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install \
        "ccxt>=4.4" "pandas>=2.2" "numpy>=1.26" "pydantic>=2.7" \
        "pyyaml>=6.0" "python-dotenv>=1.0" "rich>=13.7" \
        "fastapi>=0.110" "uvicorn[standard]>=0.30"

COPY src/ ./src/
COPY config.yaml ./

# Volumen para SQLite — Railway monta aquí.
RUN mkdir -p /app/data
ENV DATABASE_PATH=/app/data/bot.db

EXPOSE 8000

CMD ["python", "-m", "src.main"]
