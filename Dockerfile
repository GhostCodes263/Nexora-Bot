FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m bot && chown -R bot /srv
USER bot

# Migrate, then start the bot (webhook on Render, polling if no public URL).
CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
