"""Pipeline: planner → generator → evaluator for quality-driven development.

Pattern from "Harness Design for Long-Running Application Development":
- Planner expands a short spec into a full product plan
- Generator builds features with self-testing
- Evaluator grades against criteria and catches missed bugs

Key insight: "Tuning a standalone evaluator to be skeptical turns out to
be far more tractable than making a generator critical of its own work."

Communication between agents happens through files, not in-context passing.
Each agent gets a full context reset with only the structured artifacts.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import StreamEvent
from .artifacts import EvalResult, FeatureList, ProgressLog

logger = logging.getLogger(__name__)

OnEventFn = Callable[[str, StreamEvent], None] | None  # (phase, event) callback

DEFAULT_CRITERIA = {
    "functionality": "Can users complete tasks without errors? Do all features work?",
    "code_quality": "Clean architecture, no dead code, proper error handling?",
    "design_quality": "Coherent visual design, consistent spacing, good typography?",
    "completeness": "Are all specified features implemented? Any gaps?",
}

PLANNER_PROMPT = """\
You are a product planner. Take this specification and expand it into a
comprehensive product plan.

**Your job:**
1. Expand the spec into detailed feature descriptions
2. Identify technical architecture decisions
3. Create a prioritized feature list as JSON (`{feature_list}`)
4. Write the plan to `{plan_file}`

**Guidelines:**
- Be ambitious on scope
- Order features by dependency (foundations first)
- Each feature needs concrete verification steps
- Stay focused on WHAT to build, not HOW (let the builder decide)

**Output format for `{feature_list}`:**
JSON array of objects: {{"category", "description", "steps", "passes": false, "priority"}}

Specification:
{specification}
"""

GENERATOR_PROMPT = """\
You are a coding agent. Build the next features from the feature list.

**Session startup:**
1. Read `{plan_file}` for the product vision.
2. Read `{feature_list}` and pick the highest-priority unfinished features.
3. Read `{progress_file}` for context on prior work.
4. Read recent git log: `git log --oneline -20`
5. Start the dev server if an init script exists.

**During development:**
- Build and test features.
- Self-test before declaring done.
- Commit with descriptive messages.

**Before finishing:**
- Update `{feature_list}` (set passes=true only if verified).
- Update `{progress_file}` with what you did.
- Write a brief summary of changes to `{sprint_result}`.
"""

EVALUATOR_PROMPT = """\
You are a QA evaluator. Your job is to be SKEPTICAL — find bugs and gaps
that the builder missed. Do NOT trust self-reported pass status.

**Your approach:**
1. Read `{plan_file}` to understand the full spec.
2. Read `{feature_list}` to see what should be working.
3. Read `{sprint_result}` to see what was just built.
4. Start the app and TEST EVERY FEATURE as a real user would.
5. Check for errors, test edge cases, verify all claimed functionality.

**Grading criteria (score 0-10 each):**
{criteria}

**Output:** Write your evaluation to `{eval_result}` as:
{{
  "passed": true/false,
  "score": {{"criterion_name": 0-10, ...}},
  "issues": ["list of bugs and gaps found"],
  "suggestions": ["list of improvements"]
}}

**Pass threshold:** ALL criteria must score >= {pass_threshold} to pass.

Be thorough. Be skeptical.
"""


@dataclass
class PipelineConfig:
    """Configuration for the planner → generator → evaluator pipeline."""

    work_dir: str = "."
    max_rounds: int = 5
    pass_threshold: int = 7
    criteria: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CRITERIA))
    max_turns_per_agent: int = 100
    max_retries_per_phase: int = 2
    max_cost_usd: float | None = None

    # File paths
    plan_path: str = "plan.md"
    feature_list_path: str = "feature_list.json"
    progress_path: str = "claude-progress.txt"
    eval_result_path: str = "eval_result.json"
    sprint_result_path: str = "sprint_result.md"


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""

    plan_result: AgentResult | None = None
    rounds: list[dict[str, Any]] = field(default_factory=list)
    final_eval: EvalResult | None = None
    passed: bool = False
    total_cost_usd: float = 0.0


class Pipeline:
    """Planner → Generator → Evaluator pipeline.

    Usage:
        pipeline = Pipeline(config, tools, spec="Build a DAW with...")
        result = await pipeline.run()
        print(result.final_eval.summary())
    """

    def __init__(
        self,
        agent_config: CalciferConfig,
        tools: list[Tool],
        spec: str,
        pipeline_config: PipelineConfig | None = None,
        evaluator_tools: list[Tool] | None = None,
        on_event: OnEventFn = None,
    ):
        self._agent_config = agent_config
        self._tools = tools
        self._evaluator_tools = evaluator_tools or tools
        self._spec = spec
        self._config = pipeline_config or PipelineConfig()
        self._on_event = on_event
        self._total_cost = 0.0
        self._progress = ProgressLog(path=self._config.progress_path)

    def _make_config(self, **overrides: Any) -> CalciferConfig:
        return dataclasses.replace(self._agent_config, **overrides)

    async def _run_agent(self, prompt: str, tools: list[Tool], phase: str, max_turns: int | None = None) -> AgentResult:
        """Run an agent with retry, cost tracking, and event forwarding."""
        config = self._make_config(max_turns=max_turns or self._config.max_turns_per_agent)

        for attempt in range(1, self._config.max_retries_per_phase + 1):
            try:
                async with Agent(config=config, tools=tools) as agent:
                    if self._agent_config.mcp_servers:
                        await agent.connect_mcp_servers()
                    try:
                        if self._on_event:
                            result = None
                            async for event in agent.run_stream(prompt):
                                self._on_event(phase, event)
                                if event.type == "run_complete" and event.result:
                                    result = event.result
                            if result is None:
                                raise RuntimeError("Agent ended without result")
                        else:
                            result = await agent.run(prompt)
                        return result
                    finally:
                        self._total_cost += agent.cost_tracker.get_cost()
            except Exception as e:
                logger.warning("%s attempt %d failed: %s", phase, attempt, e)
                if attempt >= self._config.max_retries_per_phase:
                    raise

        raise RuntimeError("Unreachable")  # for type checker

    def _check_cost(self) -> bool:
        """Returns True if cost limit exceeded."""
        if self._config.max_cost_usd and self._total_cost >= self._config.max_cost_usd:
            logger.warning("Cost limit reached: $%.2f", self._total_cost)
            return True
        return False

    async def run_planner(self) -> AgentResult:
        prompt = PLANNER_PROMPT.format(
            specification=self._spec,
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
        )
        result = await self._run_agent(prompt, self._tools, "planner")
        self._progress.append("planner", "Planner completed.")
        return result

    async def run_generator(self, round_num: int) -> AgentResult:
        prompt = GENERATOR_PROMPT.format(
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
            progress_file=self._config.progress_path,
            sprint_result=self._config.sprint_result_path,
        )
        result = await self._run_agent(prompt, self._tools, f"generator-r{round_num}")
        self._progress.append(f"generator-r{round_num}", f"Generator round {round_num} completed.")
        return result

    async def run_evaluator(self, round_num: int) -> tuple[AgentResult, EvalResult | None]:
        criteria_text = "\n".join(
            f"- **{name}:** {desc}" for name, desc in self._config.criteria.items()
        )
        prompt = EVALUATOR_PROMPT.format(
            criteria=criteria_text,
            pass_threshold=self._config.pass_threshold,
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
            sprint_result=self._config.sprint_result_path,
            eval_result=self._config.eval_result_path,
        )
        result = await self._run_agent(prompt, self._evaluator_tools, f"evaluator-r{round_num}", max_turns=50)

        eval_result = None
        eval_path = Path(self._config.eval_result_path)
        if eval_path.exists():
            try:
                eval_result = EvalResult.load(eval_path)
            except Exception as e:
                logger.warning("Failed to parse eval result: %s", e)

        self._progress.append(
            f"evaluator-r{round_num}",
            f"Evaluator round {round_num}: {eval_result.summary() if eval_result else 'no structured result'}",
        )
        return result, eval_result

    async def run(self) -> PipelineResult:
        """Run the full pipeline: plan → (generate → evaluate) × N."""
        result = PipelineResult()

        # Phase 1: Planning (skip if both plan and feature list exist)
        plan_exists = Path(self._config.plan_path).exists()
        features_exist = Path(self._config.feature_list_path).exists()
        if not (plan_exists and features_exist):
            result.plan_result = await self.run_planner()

        # Phase 2: Generate-Evaluate loop
        for round_num in range(1, self._config.max_rounds + 1):
            if self._check_cost():
                break

            logger.info("=== Round %d/%d ===", round_num, self._config.max_rounds)

            gen_result = await self.run_generator(round_num)

            if self._check_cost():
                break

            eval_agent_result, eval_result = await self.run_evaluator(round_num)

            round_data = {
                "round": round_num,
                "generator_turns": gen_result.turn_count,
                "generator_tokens": gen_result.usage.total_tokens,
                "eval": eval_result.to_dict() if eval_result else None,
                "passed": eval_result.passed if eval_result else False,
            }
            result.rounds.append(round_data)

            if eval_result:
                result.final_eval = eval_result
                if eval_result.passed:
                    result.passed = True
                    logger.info("Pipeline PASSED on round %d", round_num)
                    break

        result.total_cost_usd = self._total_cost
        return result
