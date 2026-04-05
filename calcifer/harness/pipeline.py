"""Pipeline: planner → generator → evaluator for quality-driven development.

Pattern from "Harness Design for Long-Running Application Development":
- Planner expands a short spec into a full product plan
- Generator builds one feature at a time with self-testing
- Evaluator grades against criteria and catches bugs the generator missed

Key insight: "Tuning a standalone evaluator to be skeptical turns out to
be far more tractable than making a generator critical of its own work."

Communication between agents happens through files, not in-context passing.
Each agent gets a full context reset with only the structured artifacts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent import Agent, AgentResult
from ..config import CalciferConfig
from ..tool import Tool
from .artifacts import EvalResult, FeatureList, ProgressLog, SprintContract

logger = logging.getLogger(__name__)

# Default grading criteria (from the article)
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
1. Expand the 1-4 sentence spec into detailed feature descriptions
2. Identify technical architecture decisions
3. Create a prioritized feature list as JSON (feature_list.json)
4. Write the plan to `plan.md`

**Guidelines:**
- Be ambitious on scope — AI makes marginal cost near zero
- Identify AI integration opportunities
- Order features by dependency (foundations first)
- Each feature needs concrete verification steps
- Stay focused on WHAT to build, not HOW (let the generator decide)

**Output format for feature_list.json:**
JSON array of objects: {{"category", "description", "steps", "passes": false, "priority"}}

Specification:
{specification}
"""

GENERATOR_PROMPT = """\
You are a coding agent. Build the next feature from the feature list.

**Session startup:**
1. Read `plan.md` for the product vision.
2. Read `feature_list.json` and pick the highest-priority unfinished feature.
3. Read `claude-progress.txt` for context on prior work.
4. Read recent git log: `git log --oneline -20`
5. Start the dev server if `init.sh` exists.

**During development:**
- Implement ONE feature completely.
- Self-test before declaring done.
- Commit with descriptive messages.

**Before finishing:**
- Update `feature_list.json` (set passes=true only if verified).
- Update `claude-progress.txt` with what you did.
- Write a brief summary of changes to `sprint_result.md`.

{contract_context}
"""

EVALUATOR_PROMPT = """\
You are a QA evaluator. Your job is to be SKEPTICAL — find bugs and gaps
that the generator missed.

**Your approach:**
1. Read `plan.md` to understand the full spec.
2. Read `feature_list.json` to see what should be working.
3. Read `sprint_result.md` to see what was just built.
4. Start the app and TEST EVERY FEATURE as a real user would.
5. Use browser automation to interact with the UI.
6. Check the console for errors, test edge cases.

**Grading criteria (score 0-10 each):**
{criteria}

**Output:** Write your evaluation to `eval_result.json` as:
{{
  "passed": true/false,
  "score": {{"criterion_name": 0-10, ...}},
  "issues": ["list of bugs and gaps found"],
  "suggestions": ["list of improvements"]
}}

**Pass threshold:** ALL criteria must score >= {pass_threshold} to pass.

Be thorough. Be skeptical. The generator will praise its own work —
your job is to find what's actually broken.
"""


@dataclass
class PipelineConfig:
    """Configuration for the planner → generator → evaluator pipeline."""

    work_dir: str = "."
    max_rounds: int = 5  # max generator-evaluator cycles
    pass_threshold: int = 7  # minimum score per criterion (0-10)
    criteria: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CRITERIA))
    max_turns_per_agent: int = 100

    # File paths (relative to work_dir)
    plan_path: str = "plan.md"
    feature_list_path: str = "feature_list.json"
    progress_path: str = "claude-progress.txt"
    contract_path: str = "sprint_contract.json"
    eval_result_path: str = "eval_result.json"
    sprint_result_path: str = "sprint_result.md"


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""

    plan_result: AgentResult | None = None
    rounds: list[dict[str, Any]] = field(default_factory=list)
    final_eval: EvalResult | None = None
    passed: bool = False


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
    ):
        self._agent_config = agent_config
        self._tools = tools
        self._evaluator_tools = evaluator_tools or tools
        self._spec = spec
        self._config = pipeline_config or PipelineConfig()
        self._progress = ProgressLog(path=self._config.progress_path)

    def _make_agent(self, max_turns: int | None = None) -> Agent:
        """Create a fresh agent (full context reset)."""
        return Agent(
            config=CalciferConfig(
                api_key=self._agent_config.api_key,
                base_url=self._agent_config.base_url,
                model=self._agent_config.model,
                max_tokens=self._agent_config.max_tokens,
                temperature=self._agent_config.temperature,
                max_turns=max_turns or self._config.max_turns_per_agent,
                system_prompt=self._agent_config.system_prompt,
            ),
        )

    async def run_planner(self) -> AgentResult:
        """Run the planner agent to expand the spec."""
        prompt = PLANNER_PROMPT.format(specification=self._spec)

        async with self._make_agent() as agent:
            agent.add_tools(self._tools)
            result = await agent.run(prompt)

        self._progress.append("planner", "Planner completed. Plan and feature list created.")
        logger.info("Planner complete")
        return result

    async def run_generator(self, round_num: int) -> AgentResult:
        """Run one generator round (build next feature)."""
        # Build contract context if available
        contract_context = ""
        contract_path = Path(self._config.contract_path)
        if contract_path.exists():
            contract = SprintContract.load(contract_path)
            contract_context = f"Sprint contract:\n{contract.to_prompt()}"

        prompt = GENERATOR_PROMPT.format(contract_context=contract_context)

        async with self._make_agent() as agent:
            agent.add_tools(self._tools)
            result = await agent.run(prompt)

        self._progress.append(
            f"generator-r{round_num}",
            f"Generator round {round_num} completed.",
        )
        logger.info("Generator round %d complete", round_num)
        return result

    async def run_evaluator(self, round_num: int) -> tuple[AgentResult, EvalResult | None]:
        """Run the evaluator agent to grade the current state."""
        criteria_text = "\n".join(
            f"- **{name}:** {desc}" for name, desc in self._config.criteria.items()
        )
        prompt = EVALUATOR_PROMPT.format(
            criteria=criteria_text,
            pass_threshold=self._config.pass_threshold,
        )

        async with self._make_agent(max_turns=50) as agent:
            agent.add_tools(self._evaluator_tools)
            result = await agent.run(prompt)

        # Try to load the eval result the agent wrote
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
        logger.info(
            "Evaluator round %d: %s",
            round_num,
            eval_result.summary() if eval_result else "no result",
        )
        return result, eval_result

    async def run(self) -> PipelineResult:
        """Run the full pipeline: plan → (generate → evaluate) × N."""
        result = PipelineResult()

        # Phase 1: Planning
        if not Path(self._config.plan_path).exists():
            result.plan_result = await self.run_planner()

        # Phase 2: Generate-Evaluate loop
        for round_num in range(1, self._config.max_rounds + 1):
            logger.info("=== Round %d/%d ===", round_num, self._config.max_rounds)

            # Generator
            gen_result = await self.run_generator(round_num)

            # Evaluator
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
            else:
                logger.warning("Evaluator produced no structured result, continuing")

        return result
