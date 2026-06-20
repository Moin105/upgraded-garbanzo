from __future__ import annotations

from karst.review.findings import (
    FINDINGS_SCHEMA,
    Category,
    Finding,
    Severity,
    parse_findings,
)


def test_finding_roundtrip() -> None:
    f = Finding(
        file="src/orders.ts",
        line=14,
        severity=Severity.HIGH,
        category=Category.CORRECTNESS,
        message="Race: cart total mutated before lock acquired.",
        fix="Move calculation inside withLock().",
        confidence=0.8,
    )
    d = f.to_dict()
    assert d["severity"] == "high"
    assert d["category"] == "correctness"
    rt = Finding.from_dict(d)
    assert rt == f


def test_parse_findings_sorts_and_filters() -> None:
    raw = {
        "findings": [
            {
                "file": "a.ts",
                "line": 5,
                "severity": "low",
                "category": "style",
                "message": "nit",
            },
            {
                "file": "a.ts",
                "line": 10,
                "severity": "critical",
                "category": "security",
                "message": "SQL injection",
            },
            "not a dict",  # ignored
            {"file": "a.ts", "line": 1},  # missing required fields → ignored
        ]
    }
    out = parse_findings(raw)
    # critical first
    assert [f.severity for f in out] == [Severity.CRITICAL, Severity.LOW]


def test_schema_is_well_formed() -> None:
    # Basic shape — we want the LLM tool_use to actually accept this.
    assert FINDINGS_SCHEMA["type"] == "object"
    item_schema = FINDINGS_SCHEMA["properties"]["findings"]["items"]
    assert set(item_schema["required"]) == {"file", "line", "severity", "category", "message"}
    enum = item_schema["properties"]["severity"]["enum"]
    assert "critical" in enum and "info" in enum
