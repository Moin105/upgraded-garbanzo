"""GitHub PR integration via the `gh` CLI.

We shell out to `gh` rather than using PyGithub or the raw REST API: the
user-facing UX assumes they're already authenticated as themselves with `gh
auth`, and that avoids us juggling tokens. Phase 2 supports two operations:

    fetch_pr_diff(pr, repo) -> unified diff string
    post_review(pr, repo, findings) -> create one PR review with inline comments

Posting is gated by the caller (the CLI's --post-to-pr flag) per the spec
guidance that mutating actions need an explicit opt-in.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from .findings import Finding, Severity


class GhUnavailable(RuntimeError):
    pass


class GhError(RuntimeError):
    pass


def _gh() -> str:
    path = shutil.which("gh")
    if not path:
        raise GhUnavailable(
            "`gh` CLI not found in PATH. Install from https://cli.github.com "
            "and run `gh auth login` first."
        )
    return path


def _run(args: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(
        [_gh(), *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise GhError(
            f"gh {' '.join(args)} failed (code {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


@dataclass
class PRRef:
    number: int
    repo: str | None = None  # "owner/name", or None to use the current repo

    def as_args(self) -> list[str]:
        return [str(self.number)] + (["--repo", self.repo] if self.repo else [])


def fetch_pr_diff(pr: PRRef) -> str:
    """Return the PR's unified diff (the same one a reviewer sees on GitHub)."""
    return _run(["pr", "diff", *pr.as_args()])


def fetch_pr_head_sha(pr: PRRef) -> str:
    """Commit SHA the review will be anchored to."""
    out = _run(["pr", "view", *pr.as_args(), "--json", "headRefOid"])
    return json.loads(out)["headRefOid"]


def post_review(
    pr: PRRef,
    findings: list[Finding],
    *,
    summary: str | None = None,
    event: str = "COMMENT",
) -> None:
    """Post a single PR review with one inline comment per finding.

    Uses `gh api` against the reviews endpoint so we can create everything
    atomically (vs. one-comment-at-a-time which floods notifications).
    """
    if not pr.repo:
        # gh api needs the owner/name; resolve it from the local checkout.
        owner_name = _resolve_current_repo()
    else:
        owner_name = pr.repo

    head_sha = fetch_pr_head_sha(pr)
    body = summary or _default_summary(findings)
    comments = [_finding_to_comment(f) for f in findings if f.file and f.line > 0]

    payload = {
        "commit_id": head_sha,
        "body": body,
        "event": event,
        "comments": comments,
    }

    args = [
        "api",
        f"repos/{owner_name}/pulls/{pr.number}/reviews",
        "--method",
        "POST",
        "--input",
        "-",
    ]
    _run(args, input_text=json.dumps(payload))


def _finding_to_comment(f: Finding) -> dict:
    badge = _severity_badge(f.severity)
    body_lines = [
        f"{badge} **{f.category.value}** — {f.message}",
    ]
    if f.fix:
        body_lines.append("")
        body_lines.append(f"**Suggested fix:** {f.fix}")
    if f.confidence is not None:
        body_lines.append("")
        body_lines.append(f"_confidence: {f.confidence:.2f}_")

    comment: dict = {
        "path": f.file,
        "body": "\n".join(body_lines),
    }
    if f.end_line is not None and f.end_line > f.line:
        comment["start_line"] = f.line
        comment["start_side"] = "RIGHT"
        comment["line"] = f.end_line
        comment["side"] = "RIGHT"
    else:
        comment["line"] = f.line
        comment["side"] = "RIGHT"
    return comment


_BADGES: dict[Severity, str] = {
    Severity.CRITICAL: "🛑 CRITICAL",
    Severity.HIGH: "⚠️ HIGH",
    Severity.MEDIUM: "🟡 MEDIUM",
    Severity.LOW: "🔵 LOW",
    Severity.INFO: "ℹ️ INFO",
}


def _severity_badge(sev: Severity) -> str:
    return _BADGES.get(sev, sev.value.upper())


def _default_summary(findings: list[Finding]) -> str:
    if not findings:
        return "**coderchecker review:** no findings."
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1
    parts = [f"{n} {sev}" for sev, n in counts.items()]
    return (
        "**coderchecker review** — "
        + ", ".join(parts)
        + ". See inline comments for details."
    )


def _resolve_current_repo() -> str:
    out = _run(["repo", "view", "--json", "nameWithOwner"])
    return json.loads(out)["nameWithOwner"]
