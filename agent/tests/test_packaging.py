import json
from hashlib import sha256
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
        "**/.venv/", "**/.pytest_cache/", "**/.next/", "architecture/",
    ):
        assert exclusion in ignore
    for exclusion in (".agents/", ".claude/", ".codex/"):
        assert exclusion in docker_ignore
    assert (ROOT / "data" / "reference" / "airports_ca.csv").is_file()


def test_architecture_proof_is_truthful_and_submission_ready() -> None:
    svg = (ROOT / "architecture" / "waterline-system.svg").read_text()
    architecture = (ROOT / "ARCHITECTURE.md").read_text()
    testing = (ROOT / "TESTING.md").read_text()

    assert 'width="1920" height="1080" viewBox="0 0 1920 1080"' in svg
    for lane in (
        "Authenticated intake", "Gemini + ADK fleet", "Deterministic authority",
        "Observable consequence",
    ):
        assert lane in svg
    for capability in (
        "IDENTITY", "GATEWAY", "MODEL ARMOR", "REGISTRY", "MEMORY", "OBSERVABILITY",
    ):
        assert capability in svg
    assert svg.count('class="read-edge"') >= 5
    assert svg.count('class="human-edge"') >= 3
    assert svg.count('class="receipt-edge"') >= 4
    assert "MODEL ARMOR · DEFERRED" in svg
    assert "REGISTRY · DEFERRED" in svg
    assert "no deployment claim" in svg
    assert "This is not a claim that Google Cloud Agent Gateway is deployed" in architecture
    assert "## Rubric traceability" in architecture
    assert "## 5. Preview deployment acceptance" in testing


def test_preview_deploy_contract_is_commit_bound_private_and_firestore_only() -> None:
    script = (ROOT / "deploy" / "deploy_preview.sh").read_text()

    assert "git rev-parse --short=12 HEAD" in script
    assert "git diff --quiet" in script
    assert "waterline-agent:${revision}" in script
    assert "waterline-web:${revision}" in script
    assert "--no-allow-unauthenticated" in script
    assert "--allow-unauthenticated" in script
    assert "serviceAccount:${WL_WEB_SA}" in script
    assert "--role=roles/run.invoker" in script
    assert "WATERLINE_MODEL_LOCATION=global" in script
    assert "WATERLINE_EMBEDDING_MODEL=gemini-embedding-001" in script
    assert "WATERLINE_GEMMA_RANKER_ENABLED=true" in script
    assert "google/gemma-4-26b-a4b-it-maas" in script
    assert "FIREBASE_PROJECT_ID" in script
    assert "WATERLINE_HANDOFF_SECRET=waterline-handoff-secret:latest" in script
    assert "deploy_firestore_rules.mjs" in script
    assert "fields ttls update expiresAt" in script


def test_cloud_sql_session_secret_uses_an_async_adk_driver() -> None:
    script = (ROOT / "deploy" / "provision_cloud_sql.sh").read_text()

    assert "postgresql+psycopg://" in script
    assert "postgresql+pg8000://" not in script
    assert "?host=/cloudsql/%s" in script


def test_frontend_uses_a_runtime_private_relay_and_cloud_run_listener() -> None:
    package = json.loads((ROOT / "web" / "package.json").read_text())
    dockerfile = (ROOT / "web" / "Dockerfile").read_text()
    config = (ROOT / "web" / "next.config.ts").read_text()
    cloudbuild = (ROOT / "web" / "cloudbuild.yaml").read_text()
    relay = (ROOT / "web" / "src" / "app" / "api" / "waterline" / "[...path]" / "route.ts").read_text()
    agent_command = (ROOT / "web" / "src" / "lib" / "agent-command.ts").read_text()

    assert "0.0.0.0" in package["scripts"]["start"]
    assert "${PORT:-8080}" in package["scripts"]["start"]
    assert 'output: "standalone"' in config
    assert "NEXT_PUBLIC_AGENT_URL" not in config
    assert "ARG NEXT_PUBLIC_AGENT_URL" not in dockerfile
    assert "HOSTNAME=0.0.0.0" in dockerfile
    assert "PORT=8080" in dockerfile
    assert "NEXT_PUBLIC_AGENT_URL" not in cloudbuild
    assert "GoogleAuth" in agent_command
    assert "x-serverless-authorization" in agent_command
    assert "x-waterline-signature" in agent_command
    assert "resolvePilotSession" in relay
    assert "WATERLINE_PILOT_ACTOR" not in relay
    assert "COPY --from=builder --chown=node:node /app/public ./public" in dockerfile


def test_database_packages_the_durable_mission_state_machine() -> None:
    schema = (ROOT / "db" / "schema.sql").read_text()
    cloud_setup = (ROOT / "db" / "cloud_setup.sql").read_text()
    script = (ROOT / "deploy" / "provision_cloud_sql.sh").read_text()

    for status in (
        "proposed", "rejected", "awaiting_attestation", "corrected",
        "accepted", "dispatched",
    ):
        assert status in schema
    assert "CREATE TABLE IF NOT EXISTS mission_events" in schema
    assert "mission_events_status_check" in schema
    assert "mission_events" in cloud_setup
    assert 'GRANT USAGE, CREATE ON SCHEMA public TO :"app_user"' in cloud_setup
    assert "ON ALL TABLES IN SCHEMA public" not in cloud_setup
    assert "ON ALL SEQUENCES IN SCHEMA public" not in cloud_setup
    assert "dispatch_receipts" in cloud_setup
    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "destination_embedding vector(768)" in schema
    assert "notam_acknowledgements" in cloud_setup
    assert "--set=ON_ERROR_STOP=on" in script


def test_prepared_condition_card_is_identical_across_private_and_public_images() -> None:
    private = ROOT / "data" / "reference" / "evidence" / "lady-evelyn-condition-card-v1.png"
    public = ROOT / "web" / "public" / "evidence" / "lady-evelyn-condition-card-v1.png"
    manifest = json.loads(private.with_suffix(".json").read_text())
    private_digest = sha256(private.read_bytes()).hexdigest()

    assert private_digest == manifest["sha256"]
    assert sha256(public.read_bytes()).hexdigest() == private_digest
    assert private.stat().st_size < 5_000_000

    service = (ROOT / "agent" / "waterline" / "service.py").read_text()
    assert "UploadFile" not in service
    assert "File(" not in service


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
