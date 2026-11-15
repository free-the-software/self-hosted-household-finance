# Use an official Python runtime as a parent image
FROM python:3.13-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /app

# System packages for building Python dependencies. Receipt OCR runs in the
# separate ai-worker image, so no imaging or ML libraries are needed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# PostgreSQL client tools, used by backups (pg_dump / pg_restore). pg_dump
# must not be older than the server, so keep PG_MAJOR in step with the
# postgres image in docker-compose.yaml. The distro's own client is usually
# older, hence the PostgreSQL project's apt repository.
ARG PG_MAJOR=18
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-common ca-certificates \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && apt-get install -y --no-install-recommends postgresql-client-${PG_MAJOR} \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file separately to leverage Docker caching
COPY requirements.txt .

# Install Python dependencies
# Using --no-cache-dir reduces the image size by not saving pip cache
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Unprivileged user the app runs as. entrypoint.sh starts as root to fix
# ownership of the mounted /data folder, then drops to this user.
# UID 1000 matches the usual first user on the host, so files the app
# writes into the bind mounts stay owned by you.
ARG APP_UID=1000
RUN useradd --uid ${APP_UID} --create-home app

# The release this image is built from (scripts/update.sh and the release
# build set it from the git tag), shown in Settings and compared with the
# latest release; empty when built from code that isn't a release
ARG APP_VERSION=
ENV APP_VERSION=$APP_VERSION

# Copy the rest of the project code
# This is done last so code changes don't trigger a re-install of dependencies
COPY . .

# Copy entrypoint script explicitly and make it executable
# (It's often good to copy it separately to ensure permissions are set)
COPY ./entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]