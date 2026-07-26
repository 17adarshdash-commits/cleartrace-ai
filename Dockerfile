# Using Playwright's official Python image because it already has every
# system dependency Chromium needs pre-installed. Trying to install those
# manually on a generic Python image is a common source of deployment
# failures that are hard to debug from error messages alone — this sidesteps
# that entirely.
FROM mcr.microsoft.com/playwright/python:v1.61.0-jammy

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browser binaries are already in this base image, but this confirms the
# specific version pinned in requirements.txt is available.
RUN playwright install chromium

COPY backend/ ./backend/
COPY demo-site/ ./demo-site/
COPY demo-site-2/ ./demo-site-2/
COPY demo-site-visual/ ./demo-site-visual/

WORKDIR /app/backend

# Render sets $PORT at runtime — the app must bind to it, not a hardcoded port.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
