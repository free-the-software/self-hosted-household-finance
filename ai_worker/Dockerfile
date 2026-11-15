FROM python:3.13-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV HF_HUB_DISABLE_TELEMETRY=1 \
    MODEL_CACHE_DIR=/opt/model

# The model (about 450 MB) is part of the image, downloaded here once, so the
# worker never needs the internet. Owned by root: the worker only reads it.
COPY model.py .
RUN python model.py

COPY app.py .

# Run as an unprivileged user, and from here on offline
RUN useradd --uid 1000 --create-home app
USER app
ENV HF_HUB_OFFLINE=1

EXPOSE 5000

# One worker process: the model is large, and is loaded into memory when a
# receipt arrives (released again after MODEL_IDLE_MINUTES without one).
# Loading it and reading a receipt on a CPU can take a while, hence the long timeout.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "300", "--access-logfile", "-", "--no-control-socket", "app:app"]
