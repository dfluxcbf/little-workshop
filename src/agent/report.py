"""The agent's structured result and the seven-section change report —
ported from `little-runner`'s `report.py`. The agent writes its result
outside the repository, so it can never be committed by accident; a
missing or malformed one degrades to a report built from its own
summary, never to a failed run, since the work is already done by the
time this step runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NOT_REPORTED = "_Not reported by the agent._"

# (result-file key, report heading), in render order. Screenshot, Test
# validation results, and Pull request are appended after these four to
# make the full seven sections the pillar spec calls for.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("background", "Background"),
    ("goal", "Goal"),
    ("technical_decisions", "Technical decisions"),
    ("executed_tasks", "Executed tasks"),
)


def result_path(state_home: Path, project_id: str, run_id: str) -> Path:
    """Deliberately outside the repository, so it can never be committed."""
    return state_home / "agent-results" / f"{project_id.replace('/', '-')}-{run_id}.json"


def _text(value: Any) -> str:
    """Accept a string, a list of bullet points, or a dict of labelled parts."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            f"- {_text(v)}" if not str(v).lstrip().startswith(("-", "*", "|", "#")) else str(v).rstrip()
            for v in value if str(v).strip()
        )
    if isinstance(value, dict):
        return "\n".join(f"- **{k}**: {_text(v)}" for k, v in value.items())
    return str(value).strip()


@dataclass
class AgentResult:
    status: str = "partial"
    sections: dict[str, str] = field(default_factory=dict)
    test_results: str = ""
    source: str = "result file"

    @classmethod
    def from_summary(cls, summary: str, status: str) -> "AgentResult":
        return cls(status=status, sections={"executed_tasks": (summary or "").strip()}, source="summary")


def load_result(path: Path) -> AgentResult | None:
    """Tolerant: unknown keys ignored, missing keys empty."""
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    status = str(data.get("status", "")).strip().lower()
    result = AgentResult(status=status if status in ("done", "partial", "failed") else "partial")
    for key, _heading in SECTIONS:
        result.sections[key] = _text(data.get(key))
    result.test_results = _text(data.get("test_results"))
    return result


def clear_result(path: Path) -> None:
    path = Path(path)
    if path.exists():
        path.unlink()


def render_report(
    *,
    project_id: str,
    title: str,
    intent: str,
    branch: str,
    result: AgentResult,
    screenshots: list = (),
    screenshot_note: str = "",
    pr_url: str | None = None,
    summary: str = "",
) -> str:
    """The change report, with its fixed seven-section structure."""
    lines = [
        f"## Change report — {project_id}: {title}",
        "",
        f"Intent **{intent}** · branch `{branch}` · agent status **{result.status}**",
        "",
    ]
    if result.source != "result file":
        lines += [
            "> The agent did not leave a structured result, so this report is built from its final message.",
            "",
        ]
    for key, heading in SECTIONS:
        body = result.sections.get(key) or ""
        if key == "executed_tasks" and not body:
            body = (summary or "").strip()
        lines += [f"### {heading}", "", body or NOT_REPORTED, ""]

    lines += ["### Screenshot", ""]
    if screenshots:
        for shot in screenshots:
            lines.append(f"- [`{shot.rel_path}`]({shot.url}) — {shot.description}")
        lines += [
            "",
            "_Captured from this branch's own build, on a scratch port, against throwaway data._",
            "",
        ]
    else:
        lines += [screenshot_note or "No screenshot was captured for this change.", ""]

    lines += ["### Test validation results", "", result.test_results or NOT_REPORTED, ""]

    lines += ["### Pull request", ""]
    lines += [
        f"{pr_url} — read the diff there for code review." if pr_url
        else "_The pull request link is added once the branch is pushed._",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"
