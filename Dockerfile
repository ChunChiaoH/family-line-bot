# 3.12: line-bot-sdk's pydantic v1 layer is not compatible with 3.14+
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY family_line_bot ./family_line_bot
COPY main.py ./
RUN pip install --no-cache-dir .

ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
