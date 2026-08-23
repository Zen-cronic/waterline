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


def test_gcloud_source_contract_includes_runtime_data_and_excludes_secrets() -> None:
    ignore = (ROOT / ".gcloudignore").read_text()

    for inclusion in ("!data/reference/**", "!data/captures/**"):
        assert inclusion in ignore
    for exclusion in (
        "**/.env", "**/.env.*", "**/.venv/", "**/.pytest_cache/", "**/.next/",
    ):
        assert exclusion in ignore
    assert (ROOT / "data" / "reference" / "airports_ca.csv").is_file()
    assert list((ROOT / "data" / "captures").glob("*.json"))


def test_frontend_has_explicit_build_url_and_cloud_run_listener() -> None:
    package = json.loads((ROOT / "web" / "package.json").read_text())
    dockerfile = (ROOT / "web" / "Dockerfile").read_text()
    config = (ROOT / "web" / "next.config.ts").read_text()
    cloudbuild = (ROOT / "web" / "cloudbuild.yaml").read_text()

    assert "0.0.0.0" in package["scripts"]["start"]
    assert "${PORT:-8080}" in package["scripts"]["start"]
    assert 'output: "standalone"' in config
    assert "ARG NEXT_PUBLIC_AGENT_URL" in dockerfile
    assert "HOSTNAME=0.0.0.0" in dockerfile
    assert "PORT=8080" in dockerfile
    assert "NEXT_PUBLIC_AGENT_URL=${_NEXT_PUBLIC_AGENT_URL}" in cloudbuild
