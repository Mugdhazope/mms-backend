# syntax=docker/dockerfile:1
# Standalone backend-only image (Django + Gunicorn).
#
# Build (from this directory):
#   docker build -t mapmysutta-backend .
#
# Run — Postgres must be reachable. The entrypoint sets DATABASE_URL from:
#   POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB
# and waits for TCP on the DB host. Pass the rest of Django settings via env
# (see .envs/.local/.django or your deployment secrets).
#
# Example:
#   docker run --rm -p 5000:5000 \
#     -e POSTGRES_HOST=host.docker.internal -e POSTGRES_PORT=5432 \
#     -e POSTGRES_DB=mapmysutta -e POSTGRES_USER=... -e POSTGRES_PASSWORD=... \
#     -e DJANGO_SETTINGS_MODULE=config.settings.production \
#     mapmysutta-backend

FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS python-build-stage

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

ARG APP_HOME=/app

WORKDIR ${APP_HOME}

RUN apt-get update && apt-get install --no-install-recommends -y \
  build-essential \
  libpq-dev \
  && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . ${APP_HOME}

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev

FROM python:3.14-slim-bookworm AS python-run-stage

ARG APP_HOME=/app

WORKDIR ${APP_HOME}

RUN addgroup --system django \
  && adduser --system --ingroup django django

RUN apt-get update && apt-get install --no-install-recommends -y \
  libpq-dev \
  gettext \
  wait-for-it \
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

COPY --chown=django:django ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint && chmod +x /entrypoint

COPY --chown=django:django ./compose/production/django/start /start
RUN sed -i 's/\r$//g' /start && chmod +x /start

COPY --from=python-build-stage --chown=django:django ${APP_HOME} ${APP_HOME}

RUN mkdir -p ${APP_HOME}/mapmysutta/media \
  && chown django:django ${APP_HOME}

ENV PATH="/app/.venv/bin:$PATH"

USER django

RUN DATABASE_URL="" \
  DJANGO_SETTINGS_MODULE="config.settings.test" \
  python manage.py compilemessages

EXPOSE 5000

ENTRYPOINT ["/entrypoint"]
CMD ["/start"]
