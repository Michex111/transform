FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    libreoffice \
    pandoc \
    imagemagick \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install uv

RUN uv sync --frozen --no-dev

COPY . .

ENV PYTHONPATH=/app/src:/app

CMD ["uv", "run", "python", "-m", "workers.converter_workers.main"]