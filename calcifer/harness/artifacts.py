"""Shared artifact types for harness file-based communication.

All harness patterns use files (not in-context passing) to maintain
state across context resets. These artifacts survive agent session
boundaries and provide the structured handoff mechanism.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Feature:
    """A single feature in the feature list.

    JSON format chosen over Markdown because models are less likely
    to inappropriately overwrite JSON structures.
    """

    description: str
    category: str = "functional"  # functional, design, infrastructure
    steps: list[str] = field(default_factory=list)
    passes: bool = False
    priority: int = 0  # lower = higher priority

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Feature:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FeatureList:
    """Structured feature list persisted as JSON.

    Agents may only modify the `passes` field. Adding, removing,
    or editing feature descriptions is not allowed — this prevents
    scope drift and ensures completeness.
    """

    features: list[Feature] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([f.to_dict() for f in self.features], indent=2, ensure_ascii=False)
        )

    @classmethod
    def load(cls, path: str | Path) -> FeatureList:
        data = json.loads(Path(path).read_text())
        return cls(features=[Feature.from_dict(f) for f in data])

    @property
    def pending(self) -> list[Feature]:
        """Features not yet passing, sorted by priority."""
        return sorted(
            [f for f in self.features if not f.passes],
            key=lambda f: f.priority,
        )

    @property
    def done(self) -> list[Feature]:
        return [f for f in self.features if f.passes]

    @property
    def progress_ratio(self) -> float:
        if not self.features:
            return 0.0
        return len(self.done) / len(self.features)


@dataclass
class ProgressLog:
    """Append-only progress log for cross-session continuity.

    Each entry records what an agent did, decided, and left for next session.
    """

    path: str | Path

    def append(self, session_id: str, content: str) -> None:
        """Append a progress entry."""
        import datetime
        timestamp = datetime.datetime.now().isoformat()
        entry = f"\n## Session {session_id} — {timestamp}\n\n{content}\n"
        with open(self.path, "a") as f:
            f.write(entry)

    def read(self) -> str:
        """Read the full progress log."""
        p = Path(self.path)
        if p.exists():
            return p.read_text()
        return ""

    def read_last(self, n: int = 3) -> str:
        """Read the last N session entries."""
        content = self.read()
        sections = content.split("\n## Session ")
        if len(sections) <= n:
            return content
        return "\n## Session ".join(sections[-n:])


@dataclass
class SprintContract:
    """Agreement between generator and evaluator on what "done" means.

    Negotiated before implementation starts. Both agents must agree.
    """

    feature: str
    deliverables: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str | Path) -> SprintContract:
        data = json.loads(Path(path).read_text())
        return cls(**data)

    def to_prompt(self) -> str:
        lines = [f"## Sprint Contract: {self.feature}\n"]
        lines.append("### Deliverables")
        for d in self.deliverables:
            lines.append(f"- {d}")
        lines.append("\n### Acceptance Criteria")
        for c in self.acceptance_criteria:
            lines.append(f"- {c}")
        lines.append("\n### Verification Steps")
        for s in self.verification_steps:
            lines.append(f"- {s}")
        return "\n".join(lines)


@dataclass
class EvalResult:
    """Structured evaluation result from the evaluator agent."""

    passed: bool
    score: dict[str, float] = field(default_factory=dict)  # criterion → 0-10
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str | Path) -> EvalResult:
        data = json.loads(Path(path).read_text())
        return cls(**data)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        scores = ", ".join(f"{k}: {v:.1f}" for k, v in self.score.items())
        issues_str = "\n".join(f"  - {i}" for i in self.issues) if self.issues else "  (none)"
        return f"[{status}] Scores: {scores}\nIssues:\n{issues_str}"
