FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data \
    && chown -R app:app /app

COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py
COPY scripts ./scripts
COPY .streamlit ./.streamlit
RUN chown -R app:app /app

USER app

EXPOSE 8501 8000

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
