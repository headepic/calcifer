"""Pipeline: planner → generator → evaluator for quality-driven development.

Pattern from "Harness Design for Long-Running Application Development":
- Planner expands a short spec into a full product plan
- Generator builds features with self-testing
- Evaluator grades against criteria with few-shot calibration
- Generator reads evaluator feedback each round (closed feedback loop)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..agent import AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from ..types.message import StreamEvent
from .artifacts import EvalResult, FeatureList, ProgressLog
from .base import HarnessBase

logger = logging.getLogger(__name__)

OnEventFn = Callable[[str, StreamEvent], None] | None

DEFAULT_CRITERIA = {
    "functionality": "Can users complete tasks without errors? Do all features work end-to-end?",
    "code_quality": "Clean architecture, no dead code, proper error handling, no hacks?",
    "design_quality": "Coherent visual identity? Penalize generic AI patterns (gradient hero sections, default sans-serif, identical card layouts).",
    "completeness": "Are ALL specified features implemented? Any gaps between spec and reality?",
}

PLANNER_PROMPT = """\
You are a product planner. Take this specification and expand it into a
comprehensive product plan.

**Your job:**
1. Expand the spec into detailed feature descriptions
2. Identify technical architecture decisions
3. Create a prioritized feature list as JSON (`{feature_list}`)
   — A thorough list typically has 50-200+ items. If yours has fewer than 30, add more.
4. Write the plan to `{plan_file}`

**Guidelines:**
- Be ambitious on scope
- Order features by dependency (foundations first)
- Each feature needs concrete verification steps
- Stay focused on WHAT to build, not HOW

**Output format for `{feature_list}`:**
JSON array of objects: {{"category", "description", "steps", "passes": false, "priority"}}

Specification:
{specification}
"""

GENERATOR_PROMPT = """\
You are a coding agent. Build features from the feature list.

**Session startup (do this first):**
1. Read `{plan_file}` for the product vision.
2. Read `{feature_list}` and pick the highest-priority unfinished features.
3. Read the LAST 3 entries of `{progress_file}` (not the full file).
4. Read recent git log: `git log --oneline -20`
5. If `{eval_result}` exists, read it — the evaluator found issues you need to fix.
6. Start the dev server if an init script exists.
7. Run a baseline test to check for broken functionality. Fix bugs first.

**Evaluator feedback:**
If there is a previous evaluation, read it carefully. Fix ALL issues the evaluator
identified before building new features. If scores are improving, continue refining.
If scores are flat or declining, consider a different approach.

**During development:**
- Build and test features thoroughly.
- Test as a real user would — interact with the running application.{browser_instructions}
- NEVER delete, weaken, or modify existing tests. Fix the code instead.
- Self-test before declaring done.
- Commit with descriptive messages.

**Before finishing:**
- Update `{feature_list}` (set passes=true only if verified).
- Update `{progress_file}` with what you did.
- Write a brief summary of changes to `{sprint_result}`.

**Context anxiety:** Do not rush. Focus on quality. Commit partial progress
rather than shipping broken code.
"""

EVALUATOR_PROMPT = """\
You are a QA evaluator. Your job is to be SKEPTICAL — find bugs and gaps
that the builder missed. Do NOT trust self-reported pass status.

**Your approach:**
1. Read `{plan_file}` to understand the full spec.
2. Read `{feature_list}` to see what should be working.
3. Read `{sprint_result}` to see what was just built.
4. Start the app and TEST EVERY FEATURE as a real user would.{browser_instructions}
5. Check console/logs for errors after each interaction.
6. Test edge cases, not just happy paths.

**Grading criteria (score 0-10 each):**
{criteria}

**Scoring calibration:**
- **1-3**: Fundamentally broken. Feature doesn't work or is missing entirely.
- **4-5**: Partially works but has obvious bugs or missing pieces.
- **6-7**: Works for happy path but has edge case issues or polish gaps.
- **8-9**: Works well with minor issues. Production-ready with small fixes.
- **10**: Exceptional. No issues found.

**Output:** Write your evaluation to `{eval_result}` as:
{{
  "passed": true/false,
  "score": {{"criterion_name": 0-10, ...}},
  "issues": ["list of specific bugs and gaps found"],
  "suggestions": ["list of concrete improvements"]
}}

**Pass rule:** ALL criteria must score >= {pass_threshold}. Set "passed" accordingly.
Do NOT set "passed": true if any criterion is below {pass_threshold}.

Be thorough. Be skeptical. The builder will praise its own work —
your job is to find what's actually broken.
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


class Pipeline(HarnessBase):
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
        self._evaluator_tools = evaluator_tools or tools
        self._spec = spec
        self._config = pipeline_config or PipelineConfig()
        self._on_event = on_event
        super().__init__(
            agent_config=agent_config,
            tools=tools,
            progress_path=self._config.progress_path,
            max_retries=self._config.max_retries_per_phase,
            max_cost_usd=self._config.max_cost_usd,
        )

    async def run_planner(self) -> AgentResult:
        prompt = PLANNER_PROMPT.format(
            specification=self._spec,
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
        )
        result = await self._run_agent(
            prompt, self._tools, "planner",
            max_turns=self._config.max_turns_per_agent,
            on_event=self._on_event,
        )
        self._progress.append("planner", "Planner completed.")
        return result

    async def run_generator(self, round_num: int) -> AgentResult:
        # Snapshot feature list before generator modifies it (corruption recovery)
        FeatureList.snapshot(self._config.feature_list_path)
        pre_fl = FeatureList.load(self._config.feature_list_path)
        pre_count = len(pre_fl.features)

        browser_instructions = self._detect_browser_tools(self._tools)
        prompt = GENERATOR_PROMPT.format(
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
            progress_file=self._config.progress_path,
            sprint_result=self._config.sprint_result_path,
            eval_result=self._config.eval_result_path,
            browser_instructions=browser_instructions,
        )
        result = await self._run_agent(
            prompt, self._tools, f"generator-r{round_num}",
            max_turns=self._config.max_turns_per_agent,
            on_event=self._on_event,
        )

        # Post-round: validate feature list integrity
        post_fl = FeatureList.load(self._config.feature_list_path)
        if len(post_fl.features) > 0 and len(post_fl.features) < pre_count * 0.5:
            logger.warning(
                "Feature list shrank from %d to %d in round %d — possible corruption",
                pre_count, len(post_fl.features), round_num,
            )

        self._progress.append(f"generator-r{round_num}", f"Generator round {round_num} completed.")
        return result

    async def run_evaluator(self, round_num: int) -> tuple[AgentResult, EvalResult | None]:
        criteria_text = "\n".join(
            f"- **{name}:** {desc}" for name, desc in self._config.criteria.items()
        )
        browser_instructions = self._detect_browser_tools(self._evaluator_tools)
        prompt = EVALUATOR_PROMPT.format(
            criteria=criteria_text,
            pass_threshold=self._config.pass_threshold,
            feature_list=self._config.feature_list_path,
            plan_file=self._config.plan_path,
            sprint_result=self._config.sprint_result_path,
            eval_result=self._config.eval_result_path,
            browser_instructions=browser_instructions,
        )
        result = await self._run_agent(
            prompt, self._evaluator_tools, f"evaluator-r{round_num}",
            max_turns=50,
            on_event=self._on_event,
        )

        # Load and validate eval result (EvalResult.load handles errors internally)
        eval_result = EvalResult.load(self._config.eval_result_path)
        if eval_result and eval_result.score:
            # Programmatic enforcement: override agent's passed flag
            all_pass = all(
                s >= self._config.pass_threshold
                for s in eval_result.score.values()
            )
            if eval_result.passed != all_pass:
                logger.warning(
                    "Evaluator claimed passed=%s but scores say %s — overriding",
                    eval_result.passed, all_pass,
                )
                eval_result.passed = all_pass

        self._progress.append(
            f"evaluator-r{round_num}",
            f"Evaluator round {round_num}: {eval_result.summary() if eval_result else 'no structured result'}",
        )
        return result, eval_result

    async def run(self) -> PipelineResult:
        """Run the full pipeline: plan → (generate → evaluate) × N."""
        result = PipelineResult()

        # Phase 1: Planning
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
