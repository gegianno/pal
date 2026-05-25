from __future__ import annotations

import pytest

from pal.flow.evidence import (
    EvidenceRequirement,
    EvidenceStatus,
    FlowEvidence,
    FlowReadiness,
    normalize_evidence_check,
)
from pal.flow.models import FlowPhase


def test_flow_evidence_round_trips_optional_fields_and_normalizes_check() -> None:
    parsed = FlowEvidence.from_dict(
        {
            "evidence_id": "evidence_1",
            "run_id": "run_1",
            "phase": "verify",
            "check": "browser",
            "status": "passed",
            "summary": "Browser check passed.",
            "created_at": "2026-04-27T00:00:00Z",
        }
    )

    assert parsed.to_dict() == {
        "evidence_id": "evidence_1",
        "run_id": "run_1",
        "phase": "verify",
        "check": "browser",
        "status": "passed",
        "summary": "Browser check passed.",
        "details": "",
        "url": "",
        "artifact": "",
        "actor": "",
        "created_at": "2026-04-27T00:00:00Z",
    }


def test_flow_readiness_serializes_requirements_and_evidence() -> None:
    evidence = FlowEvidence(
        evidence_id="evidence_1",
        run_id="run_1",
        phase=FlowPhase.VERIFY,
        check="verification",
        status=EvidenceStatus.WAIVED,
        summary="Accepted by QA.",
        details="",
        url="",
        artifact="",
        actor="human",
        created_at="2026-04-27T00:00:00Z",
    )
    readiness = FlowReadiness(
        run_id="run_1",
        status="ready",
        blockers=[],
        warnings=["Waived evidence."],
        requirements=[
            EvidenceRequirement(
                phase=FlowPhase.VERIFY,
                check="verification",
                reason="Browser validation was blocked.",
            )
        ],
        evidence=[evidence],
    )

    assert readiness.ready is True
    assert readiness.to_dict()["requirements"] == [
        {
            "phase": "verify",
            "check": "verification",
            "reason": "Browser validation was blocked.",
        }
    ]
    assert readiness.to_dict()["evidence"] == [evidence.to_dict()]
    assert FlowReadiness(run_id="run_1", status="not_ready").ready is False


def test_normalize_evidence_check_rejects_unsafe_names() -> None:
    assert normalize_evidence_check("browser-check") == "browser-check"
    with pytest.raises(ValueError, match="Evidence check"):
        normalize_evidence_check("../browser")
