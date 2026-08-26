import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_agent_container_uses_locked_poetry_and_cloud_run_contract() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "agent/pyproject.toml agent/poetry.lock" in dockerfile
    assert "poetry install --only main --no-root" in dockerfile
    assert "google-adk[db]" not in dockerfile
    assert "COPY --chown=waterline:waterline data /app/data" in dockerfile
    assert "USER waterline" in dockerfile
    assert "python -m uvicorn waterline.service:app --host 0.0.0.0 --port ${PORT}" in dockerfile
    assert (ROOT / "agent" / "poetry.lock").is_file()


def test_gcloud_source_contract_includes_reference_data_and_excludes_captures() -> None:
    ignore = (ROOT / ".gcloudignore").read_text()
    docker_ignore = (ROOT / ".dockerignore").read_text()

    assert "!data/reference/**" in ignore
    assert "!data/captures/**" not in ignore
    assert "!data/captures/**" not in docker_ignore
    for exclusion in (
        ".agents/", ".claude/", ".codex/", "**/.env", "**/.env.*",
        "**/.venv/", "**/.pytest_cache/", "**/.next/",
    ):
        assert exclusion in ignore
    for exclusion in (".agents/", ".claude/", ".codex/"):
        assert exclusion in docker_ignore
    assert (ROOT / "data" / "reference" / "airports_ca.csv").is_file()


def test_frontend_uses_a_runtime_private_relay_and_cloud_run_listener() -> None:
    package = json.loads((ROOT / "web" / "package.json").read_text())
    dockerfile = (ROOT / "web" / "Dockerfile").read_text()
    config = (ROOT / "web" / "next.config.ts").read_text()
    cloudbuild = (ROOT / "web" / "cloudbuild.yaml").read_text()
    relay = (ROOT / "web" / "src" / "app" / "api" / "waterline" / "[...path]" / "route.ts").read_text()

    assert "0.0.0.0" in package["scripts"]["start"]
    assert "${PORT:-8080}" in package["scripts"]["start"]
    assert 'output: "standalone"' in config
    assert "NEXT_PUBLIC_AGENT_URL" not in config
    assert "ARG NEXT_PUBLIC_AGENT_URL" not in dockerfile
    assert "HOSTNAME=0.0.0.0" in dockerfile
    assert "PORT=8080" in dockerfile
    assert "NEXT_PUBLIC_AGENT_URL" not in cloudbuild
    assert "GoogleAuth" in relay
    assert "x-serverless-authorization" in relay
    assert "x-waterline-signature" in relay
    assert "resolvePilotSession" in relay
    assert "WATERLINE_PILOT_ACTOR" not in relay


def test_database_packages_the_durable_mission_state_machine() -> None:
    schema = (ROOT / "db" / "schema.sql").read_text()
    cloud_setup = (ROOT / "db" / "cloud_setup.sql").read_text()

    for status in (
        "proposed", "rejected", "awaiting_attestation", "corrected",
        "accepted", "dispatched",
    ):
        assert status in schema
    assert "CREATE TABLE IF NOT EXISTS mission_events" in schema
    assert "mission_events_status_check" in schema
    assert "mission_events" in cloud_setup


def test_product_copy_matches_the_curated_destination_scope() -> None:
    copy = "\n".join(
        (ROOT / path).read_text()
        for path in ("README.md", "web/src/app/layout.tsx", "web/src/app/page.tsx")
    )

    assert "446" not in copy
    assert "449" not in copy
    for destination in (
        "Lady Evelyn Lake", "Lake Temagami", "Biscotasi Lake", "Wabikon Lake",
        "Smoothwater Lake",
    ):
        assert destination in copy
