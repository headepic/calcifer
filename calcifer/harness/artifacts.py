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

# Unique delimiter for progress log sections (not likely in agent output)
PROGRESS_DELIMITER = "---CALCIFER_SESSION_BOUNDARY---"


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
    def from_dict(cls, d: dict[str, Any]) -> Feature | None:
        """Build Feature from dict. Returns None if required fields are missing."""
        if not isinstance(d, dict) or not d.get("description"):
            return None
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
        """Load feature list from JSON with corruption detection.

        Distinguishes: file not found (returns empty), parse error (logs ERROR,
        returns empty), structural corruption (drops corrupt entries + warns).
        """
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("CORRUPT feature list %s: %s — recovery needed", path, e)
            return cls()
        if not isinstance(data, list):
            logger.error("CORRUPT feature list %s: expected array, got %s", path, type(data).__name__)
            return cls()
        features = []
        for i, item in enumerate(data):
            f = Feature.from_dict(item) if isinstance(item, dict) else None
            if f is not None:
                features.append(f)
            else:
                logger.warning("Feature list %s: dropping corrupt entry at index %d", path, i)
        return cls(features=features)

    @staticmethod
    def snapshot(path: str | Path) -> None:
        """Save a timestamped backup before a session modifies the feature list."""
        import shutil
        import datetime
        src = Path(path)
        if src.exists():
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = src.with_suffix(f".{ts}.bak")
            shutil.copy2(src, backup)

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
    Uses a unique delimiter to prevent agent output from corrupting parsing.
    """

    path: str | Path

    def append(self, session_id: str, content: str) -> None:
        """Append a progress entry."""
        import datetime
        timestamp = datetime.datetime.now().isoformat()
        entry = f"\n{PROGRESS_DELIMITER}\n## Session {session_id} — {timestamp}\n\n{content}\n"
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
        sections = content.split(PROGRESS_DELIMITER)
        if len(sections) <= n:
            return content
        return PROGRESS_DELIMITER.join(sections[-n:])


@dataclass
class EvalResult:
    """Structured evaluation result from the evaluator agent."""

    passed: bool
    score: dict[str, float] = field(default_factory=dict)  # criterion → 0-10
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        """Save eval result to JSON. Utility for programmatic use."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str | Path) -> EvalResult | None:
        """Load eval result from JSON. Returns None on parse failure."""
        p = Path(path)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
            if not isinstance(data, dict):
                logger.warning("Eval result %s: expected object, got %s", path, type(data).__name__)
                return None
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning("Failed to load eval result from %s: %s", path, e)
            return None

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        scores = ", ".join(f"{k}: {v:.1f}" for k, v in self.score.items())
        issues_str = "\n".join(f"  - {i}" for i in self.issues) if self.issues else "  (none)"
        return f"[{status}] Scores: {scores}\nIssues:\n{issues_str}"
