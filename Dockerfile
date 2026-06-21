FROM python:3.11-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY stratfile.schema.json ./
COPY examples ./examples
COPY fixtures ./fixtures

RUN pip install .

EXPOSE 8080 8501

CMD ["stratfile", "--help"]
