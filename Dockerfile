# Waterline agent service — Cloud Run target. Build from the project root:
#   docker build -t waterline-agent -f Dockerfile .
FROM python:3.12-slim AS builder

ARG POETRY_VERSION=2.2.1
ENV POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build/agent

# pip installs only the pinned build tool. Application dependencies come
# exclusively from pyproject.toml + poetry.lock below.
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
COPY agent/pyproject.toml agent/poetry.lock ./
RUN poetry install --only main --no-root --no-ansi

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8080
WORKDIR /app/agent

RUN groupadd --system waterline && useradd --system --gid waterline --home /app/agent waterline
COPY --from=builder /build/agent/.venv /opt/venv
COPY --chown=waterline:waterline agent/waterline ./waterline
# Preserve the repository layout expected by route_tools.py/navcanada.py.
# data/reference contains the public-domain airport dataset and Waterline's
# tracked synthetic evidence. Local NAV CANADA captures are deliberately
# excluded from deployment by both ignore contracts.
COPY --chown=waterline:waterline data /app/data
RUN mkdir -p /app/agent/data/outbox && chown -R waterline:waterline /app/agent/data

USER waterline
EXPOSE 8080
CMD ["sh", "-c", "python -m uvicorn waterline.service:app --host 0.0.0.0 --port ${PORT}"]
