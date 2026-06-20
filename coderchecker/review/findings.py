"""Code-review finding schema (spec §8).

Findings are the agent's reviewer-grade output: severity-tagged, anchored to
a file + line, with optional fix suggestion. The JSON schema doubles as the
LLM tool_use input_schema, so the model is forced into this shape rather
than producing free-form prose.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"   # will crash prod or expose data
    HIGH = "high"           # likely incorrect under normal load
    MEDIUM = "medium"       # smell, dead code, leak risk
    LOW = "low"             # style, naming, micro-perf
    INFO = "info"           # refactor suggestion, FYI

    @property
    def rank(self) -> int:
        return {
            Severity.CRITICAL: 5,
            Severity.HIGH: 4,
            Severity.MEDIUM: 3,
            Severity.LOW: 2,
            Severity.INFO: 1,
        }[self]


class Category(str, Enum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    DESIGN = "design"
    STYLE = "style"
    TESTING = "testing"
    OTHER = "other"


@dataclass
class Finding:
    file: str
    line: int
    severity: Severity
    category: Category
    message: str
    fix: str | None = None
    end_line: int | None = None        # for ranged comments
    confidence: float | None = None    # spec §11 confidence calibration

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["category"] = self.category.value
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Finding":
        return cls(
            file=str(raw["file"]),
            line=int(raw["line"]),
            severity=Severity(str(raw["severity"]).lower()),
            category=Category(str(raw.get("category", "other")).lower()),
            message=str(raw["message"]),
            fix=raw.get("fix"),
            end_line=int(raw["end_line"]) if raw.get("end_line") is not None else None,
            confidence=float(raw["confidence"]) if raw.get("confidence") is not None else None,
        )


# JSON Schema used as the LLM tool_use input_schema. Kept tight so the model
# can't wander off and produce invalid records.
FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "description": (
                "Zero or more reviewer findings. Empty when the diff is clean. "
                "Each finding must anchor to a real file path and line that exists "
                "in the diff."
            ),
            "items": {
                "type": "object",
                "required": ["file", "line", "severity", "category", "message"],
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Repo-relative path of the changed file.",
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "1-based line number in the POST-image (new file).",
                    },
                    "end_line": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional inclusive end line for a ranged finding.",
                    },
                    "severity": {
                        "type": "string",
                        "enum": [s.value for s in Severity],
                    },
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in Category],
                    },
                    "message": {
                        "type": "string",
                        "description": "What is wrong, in one to three sentences.",
                    },
                    "fix": {
                        "type": ["string", "null"],
                        "description": "Optional concrete fix suggestion.",
                    },
                    "confidence": {
                        "type": ["number", "null"],
                        "minimum": 0,
                        "maximum": 1,
                        "description": (
                            "0-1. Below 0.4 is queued for human review per spec §11."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def parse_findings(raw: dict[str, Any]) -> list[Finding]:
    items = raw.get("findings") or []
    out: list[Finding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Finding.from_dict(item))
        except (KeyError, ValueError):
            continue
    # Sort by severity (worst first) then file/line for stable output.
    out.sort(key=lambda f: (-f.severity.rank, f.file, f.line))
    return out
