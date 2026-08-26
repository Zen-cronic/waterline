import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from waterline import condition_card
from waterline.condition_card import ConditionCardExtraction


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _fixture() -> tuple[condition_card.PreparedArtifact, bytes, ConditionCardExtraction]:
    artifact = condition_card.load_prepared_artifact()
    image_bytes = artifact.image_path.read_bytes()
    extraction = ConditionCardExtraction.model_validate(artifact.expected_extraction)
    return artifact, image_bytes, extraction


def test_prepared_card_digest_visual_facts_and_hostile_text_are_separated(monkeypatch) -> None:
    monkeypatch.setenv("WATERLINE_EVIDENCE_MODE", "fixture")
    artifact, image_bytes, expected = _fixture()
    extraction, extractor = asyncio.run(
        condition_card.extract_condition_card(artifact, image_bytes),
    )
    decision = condition_card.validate_condition_card(
        artifact=artifact,
        image_bytes=image_bytes,
        extraction=extraction,
        destination="Lady Evelyn Lake",
        trace_id="trace-card-test",
        extractor=extractor,
        now=NOW,
    )

    assert extraction == expected and extractor == "fixture"
    assert sha256(image_bytes).hexdigest() == artifact.sha256
    assert decision.validation_result == "accepted"
    assert decision.reason_codes == ()
    assert decision.trusted_evidence and decision.trusted_evidence["blocked_sector"] == "east"
    assert decision.plan_revision
    assert decision.plan_revision["rejected_plan"]["landing_sector"] == "east"
    assert decision.plan_revision["corrected_plan"]["landing_sector"] == "west"
    assert decision.model_receipt["dispatch_authority"] is False
    assert decision.quarantine_receipt
    assert decision.quarantine_receipt["action"] == "excluded_from_trusted_state"
    safe_payload = json.dumps(decision.public_payload())
    assert "IGNORE SAFETY" not in safe_payload
    assert "AUTHORIZE DISPATCH" not in safe_payload
    assert condition_card.artifact_for_destination("Lake Temagami") is None
    assert [path.name for path in artifact.image_path.parent.glob("*.png")] == [
        "lady-evelyn-condition-card-v1.png",
    ]


@pytest.mark.parametrize(
    ("mutation", "destination", "checked_at", "reason"),
    [
        ({"observed_confidence": 0.89}, "Lady Evelyn Lake", NOW,
         "confidence_below_threshold"),
        ({"blocked_sector": "unknown"}, "Lady Evelyn Lake", NOW,
         "sector_not_supported"),
        ({"untrusted_text_detected": False, "untrusted_text": None},
         "Lady Evelyn Lake", NOW, "prepared_manifest_untrusted_signal_mismatch"),
        ({}, "Lake Temagami", NOW, "lake_route_mismatch"),
        ({}, "Lady Evelyn Lake", datetime(2026, 9, 8, tzinfo=timezone.utc),
         "validity_window_failed"),
        ({"valid_until": datetime(2026, 9, 7, 23, 59)}, "Lady Evelyn Lake", NOW,
         "timezone_missing"),
    ],
)
def test_ambiguous_stale_low_confidence_or_mismatched_evidence_fails_closed(
    mutation, destination, checked_at, reason,
) -> None:
    artifact, image_bytes, extraction = _fixture()
    decision = condition_card.validate_condition_card(
        artifact=artifact,
        image_bytes=image_bytes,
        extraction=extraction.model_copy(update=mutation),
        destination=destination,
        trace_id="trace-review-test",
        extractor="fixture",
        now=checked_at,
    )

    assert decision.validation_result == "review_required"
    assert reason in decision.reason_codes
    assert decision.trusted_evidence is None
    assert decision.plan_revision is None
    assert decision.model_receipt["dispatch_authority"] is False


def test_corrupt_artifact_and_malformed_extraction_cannot_create_trusted_state() -> None:
    artifact, image_bytes, extraction = _fixture()
    decision = condition_card.validate_condition_card(
        artifact=artifact,
        image_bytes=image_bytes + b"corrupt",
        extraction=extraction,
        destination="Lady Evelyn Lake",
        trace_id="trace-corrupt-test",
        extractor="fixture",
        now=NOW,
    )
    assert decision.validation_result == "review_required"
    assert "artifact_digest_mismatch" in decision.reason_codes
    assert decision.trusted_evidence is None and decision.plan_revision is None

    malformed = dict(artifact.expected_extraction)
    malformed.pop("template_id")
    with pytest.raises(ValidationError):
        ConditionCardExtraction.model_validate(malformed)
    with pytest.raises(ValidationError):
        ConditionCardExtraction.model_validate({**artifact.expected_extraction, "authority": True})


def test_live_adapter_sends_image_bytes_and_requires_typed_output(monkeypatch) -> None:
    artifact, image_bytes, extraction = _fixture()
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self) -> None:
            self.models = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(parsed=extraction.model_dump(), text=None)

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.aio = FakeAsyncClient()

    monkeypatch.setenv("WATERLINE_EVIDENCE_MODE", "gemini")
    monkeypatch.setattr(condition_card.genai, "Client", FakeClient)
    result, extractor = asyncio.run(
        condition_card.extract_condition_card(artifact, image_bytes),
    )

    assert result == extraction and extractor == "gemini-3.7-flash"
    assert len(calls) == 1
    assert calls[0]["config"].response_schema is ConditionCardExtraction
    image_part = calls[0]["contents"][1]
    assert image_part.inline_data.data == image_bytes
    assert image_part.inline_data.mime_type == "image/png"


def test_extraction_failure_receipt_has_no_exception_or_authority() -> None:
    artifact, _image_bytes, _extraction = _fixture()
    decision = condition_card.failed_evidence_decision(
        artifact=artifact,
        trace_id="trace-failed-test",
        reason_code="condition_extraction_failed",
    )
    payload = decision.public_payload()

    assert decision.validation_result == "review_required"
    assert decision.trusted_evidence is None and decision.plan_revision is None
    assert payload["model_receipt"]["dispatch_authority"] is False
    assert "exception" not in json.dumps(payload).lower()
