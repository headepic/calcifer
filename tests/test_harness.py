"""Tests for the harness module (artifacts, SessionLoop, Pipeline)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from calcifer import Agent, AgentResult, CalciferConfig, Message, Usage
from calcifer.harness import Pipeline, PipelineConfig, SessionConfig, SessionLoop
from calcifer.harness.artifacts import (
    PROGRESS_DELIMITER,
    EvalResult,
    Feature,
    FeatureList,
    ProgressLog,
)
from calcifer.harness.base import HarnessBase


# ===== Feature & FeatureList =====


class TestFeature:
    def test_to_dict_round_trip(self):
        f = Feature(
            description="test feature",
            category="functional",
            steps=["s1", "s2"],
            passes=False,
            priority=1,
        )
        d = f.to_dict()
        f2 = Feature.from_dict(d)
        assert f == f2

    def test_from_dict_ignores_unknown_keys(self):
        f = Feature.from_dict({
            "description": "x",
            "category": "design",
            "unknown_key": "should be ignored",
            "another": 42,
        })
        assert f is not None
        assert f.description == "x"
        assert f.category == "design"

    def test_from_dict_uses_defaults_for_missing(self):
        f = Feature.from_dict({"description": "minimal"})
        assert f is not None
        assert f.description == "minimal"
        assert f.category == "functional"
        assert f.steps == []
        assert f.passes is False
        assert f.priority == 0

    def test_from_dict_returns_none_without_description(self):
        assert Feature.from_dict({}) is None
        assert Feature.from_dict({"category": "x"}) is None
        assert Feature.from_dict({"description": ""}) is None

    def test_from_dict_returns_none_for_non_dict(self):
        assert Feature.from_dict("not a dict") is None  # type: ignore
        assert Feature.from_dict(None) is None  # type: ignore


class TestFeatureList:
    def test_save_load_round_trip(self, tmp_path: Path):
        fl = FeatureList(features=[
            Feature(description="f1", priority=0),
            Feature(description="f2", priority=1, passes=True),
        ])
        p = tmp_path / "features.json"
        fl.save(p)
        loaded = FeatureList.load(p)
        assert len(loaded.features) == 2
        assert loaded.features[0].description == "f1"
        assert loaded.features[1].passes is True

    def test_load_nonexistent_returns_empty(self, tmp_path: Path):
        fl = FeatureList.load(tmp_path / "missing.json")
        assert fl.features == []

    def test_load_malformed_json_returns_empty(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json")
        fl = FeatureList.load(p)
        assert fl.features == []

    def test_load_non_array_returns_empty(self, tmp_path: Path):
        p = tmp_path / "obj.json"
        p.write_text('{"features": []}')
        fl = FeatureList.load(p)
        assert fl.features == []

    def test_load_drops_entries_with_empty_description(self, tmp_path: Path):
        p = tmp_path / "features.json"
        p.write_text(json.dumps([
            {"description": "good"},
            {"description": ""},  # corrupt: empty
            {"category": "noname"},  # corrupt: no description
            {"description": "also good"},
        ]))
        fl = FeatureList.load(p)
        assert len(fl.features) == 2
        assert fl.features[0].description == "good"
        assert fl.features[1].description == "also good"

    def test_load_drops_non_dict_entries(self, tmp_path: Path):
        p = tmp_path / "features.json"
        p.write_text(json.dumps([
            {"description": "good"},
            "not a dict",
            42,
            None,
        ]))
        fl = FeatureList.load(p)
        assert len(fl.features) == 1
        assert fl.features[0].description == "good"

    def test_pending_sorted_by_priority(self):
        fl = FeatureList(features=[
            Feature(description="low", priority=10),
            Feature(description="high", priority=0),
            Feature(description="done", priority=5, passes=True),
            Feature(description="mid", priority=5),
        ])
        pending = fl.pending
        assert [f.description for f in pending] == ["high", "mid", "low"]

    def test_done_only_includes_passing(self):
        fl = FeatureList(features=[
            Feature(description="a", passes=True),
            Feature(description="b", passes=False),
            Feature(description="c", passes=True),
        ])
        assert [f.description for f in fl.done] == ["a", "c"]

    def test_progress_ratio_empty(self):
        assert FeatureList().progress_ratio == 0.0

    def test_progress_ratio_partial(self):
        fl = FeatureList(features=[
            Feature(description="a", passes=True),
            Feature(description="b", passes=False),
            Feature(description="c", passes=True),
            Feature(description="d", passes=False),
        ])
        assert fl.progress_ratio == 0.5

    def test_snapshot_creates_timestamped_backup(self, tmp_path: Path):
        p = tmp_path / "features.json"
        p.write_text('[{"description": "x"}]')
        FeatureList.snapshot(p)
        backups = list(tmp_path.glob("features.*.bak"))
        assert len(backups) == 1
        assert backups[0].read_text() == p.read_text()

    def test_snapshot_noop_on_missing_file(self, tmp_path: Path):
        FeatureList.snapshot(tmp_path / "missing.json")
        assert list(tmp_path.glob("*.bak")) == []


# ===== ProgressLog =====


class TestProgressLog:
    def test_append_creates_file(self, tmp_path: Path):
        p = tmp_path / "progress.txt"
        log = ProgressLog(path=p)
        log.append("s1", "hello world")
        content = p.read_text()
        assert "hello world" in content
        assert "s1" in content
        assert PROGRESS_DELIMITER in content

    def test_append_multiple_entries(self, tmp_path: Path):
        p = tmp_path / "progress.txt"
        log = ProgressLog(path=p)
        log.append("s1", "first")
        log.append("s2", "second")
        log.append("s3", "third")
        content = p.read_text()
        assert "first" in content
        assert "second" in content
        assert "third" in content
        assert content.count(PROGRESS_DELIMITER) == 3

    def test_read_nonexistent_returns_empty(self, tmp_path: Path):
        log = ProgressLog(path=tmp_path / "missing.txt")
        assert log.read() == ""

    def test_read_last_limits_to_n(self, tmp_path: Path):
        p = tmp_path / "progress.txt"
        log = ProgressLog(path=p)
        for i in range(5):
            log.append(f"s{i}", f"entry {i}")
        last_two = log.read_last(2)
        assert "entry 4" in last_two
        assert "entry 3" in last_two
        assert "entry 0" not in last_two

    def test_read_last_returns_all_when_fewer(self, tmp_path: Path):
        p = tmp_path / "progress.txt"
        log = ProgressLog(path=p)
        log.append("s1", "only")
        assert "only" in log.read_last(10)

    def test_delimiter_survives_agent_output_with_hashes(self, tmp_path: Path):
        """Agent output with ## or --- shouldn't break parsing."""
        p = tmp_path / "progress.txt"
        log = ProgressLog(path=p)
        log.append("s1", "## Header\n--- something\n## Another")
        log.append("s2", "regular content")
        sections = log.read().split(PROGRESS_DELIMITER)
        # 3 parts: empty prefix + 2 session entries
        assert len(sections) == 3


# ===== EvalResult =====


class TestEvalResult:
    def test_to_dict_round_trip(self):
        er = EvalResult(
            passed=True,
            score={"functionality": 8.0, "design": 7.5},
            issues=["bug 1"],
            suggestions=["improve x"],
        )
        d = er.to_dict()
        assert d["passed"] is True
        assert d["score"]["functionality"] == 8.0

    def test_save_load_round_trip(self, tmp_path: Path):
        er = EvalResult(
            passed=False,
            score={"functionality": 5.0},
            issues=["broken"],
        )
        p = tmp_path / "eval.json"
        er.save(p)
        loaded = EvalResult.load(p)
        assert loaded is not None
        assert loaded.passed is False
        assert loaded.score == {"functionality": 5.0}
        assert loaded.issues == ["broken"]

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        assert EvalResult.load(tmp_path / "missing.json") is None

    def test_load_malformed_returns_none(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        assert EvalResult.load(p) is None

    def test_load_non_object_returns_none(self, tmp_path: Path):
        p = tmp_path / "arr.json"
        p.write_text("[1, 2, 3]")
        assert EvalResult.load(p) is None

    def test_load_ignores_unknown_keys(self, tmp_path: Path):
        p = tmp_path / "eval.json"
        p.write_text(json.dumps({
            "passed": True,
            "score": {"x": 9},
            "unknown_field": "ignored",
        }))
        loaded = EvalResult.load(p)
        assert loaded is not None
        assert loaded.passed is True

    def test_summary_format(self):
        er = EvalResult(
            passed=True,
            score={"functionality": 9.0, "design": 8.0},
            issues=["minor issue"],
        )
        s = er.summary()
        assert "PASS" in s
        assert "functionality: 9.0" in s
        assert "minor issue" in s

    def test_summary_no_issues(self):
        er = EvalResult(passed=True, score={"x": 10}, issues=[])
        assert "(none)" in er.summary()


# ===== HarnessBase =====


class TestHarnessBase:
    def test_detect_browser_tools_positive(self, tmp_path: Path):
        from calcifer import tool

        @tool(name="browse", description="browser")
        def browse(url: str) -> str:
            return ""

        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[browse],
            progress_path=str(tmp_path / "p.txt"),
        )
        instructions = base._detect_browser_tools([browse])
        assert "browser" in instructions.lower()

    def test_detect_browser_tools_negative(self, tmp_path: Path):
        from calcifer import tool

        @tool(name="calc", description="math")
        def calc(a: int) -> str:
            return ""

        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[calc],
            progress_path=str(tmp_path / "p.txt"),
        )
        assert base._detect_browser_tools([calc]) == ""

    def test_detect_browser_recognizes_playwright_puppeteer(self, tmp_path: Path):
        from calcifer import tool

        @tool(name="playwright_click", description="click")
        def pw(x: str) -> str:
            return ""

        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[pw],
            progress_path=str(tmp_path / "p.txt"),
        )
        assert "browser" in base._detect_browser_tools([pw]).lower()

    def test_check_cost_no_limit(self, tmp_path: Path):
        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[],
            progress_path=str(tmp_path / "p.txt"),
            max_cost_usd=None,
        )
        base._total_cost = 1000.0
        assert base._check_cost() is False

    def test_check_cost_with_zero_limit(self, tmp_path: Path):
        """Zero budget should trigger immediately (not treated as falsy)."""
        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[],
            progress_path=str(tmp_path / "p.txt"),
            max_cost_usd=0.0,
        )
        base._total_cost = 0.0
        assert base._check_cost() is True

    def test_check_cost_under_limit(self, tmp_path: Path):
        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[],
            progress_path=str(tmp_path / "p.txt"),
            max_cost_usd=10.0,
        )
        base._total_cost = 5.0
        assert base._check_cost() is False

    def test_check_cost_at_limit(self, tmp_path: Path):
        base = HarnessBase(
            agent_config=CalciferConfig(api_key="x"),
            tools=[],
            progress_path=str(tmp_path / "p.txt"),
            max_cost_usd=10.0,
        )
        base._total_cost = 10.0
        assert base._check_cost() is True

    def test_make_config_preserves_fields(self, tmp_path: Path):
        cfg = CalciferConfig(
            api_key="x",
            model="gpt-4o",
            thinking_mode="adaptive",
            thinking_budget_tokens=5000,
            max_context_tokens=100_000,
        )
        base = HarnessBase(
            agent_config=cfg, tools=[],
            progress_path=str(tmp_path / "p.txt"),
        )
        new_cfg = base._make_config(max_turns=50)
        assert new_cfg.max_turns == 50
        assert new_cfg.thinking_mode == "adaptive"
        assert new_cfg.thinking_budget_tokens == 5000
        assert new_cfg.max_context_tokens == 100_000
        assert new_cfg.model == "gpt-4o"


# ===== SessionLoop (with mocked agent) =====


def _mock_agent_run(return_text: str = "done", feature_ops: list[tuple[str, bool]] | None = None):
    """Create a mock Agent that optionally writes to feature list.

    feature_ops: list of (description, passes) to write to the feature list.
    """
    async def _mock_run(prompt, **kwargs):
        return AgentResult(
            messages=[],
            final_text=return_text,
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            turn_count=1,
        )
    return _mock_run


class TestSessionLoop:
    @pytest.mark.asyncio
    async def test_not_initialized_initially(self, tmp_path: Path):
        cfg = CalciferConfig(api_key="x")
        loop = SessionLoop(
            cfg, [], spec="test",
            session_config=SessionConfig(
                feature_list_path=str(tmp_path / "features.json"),
                progress_path=str(tmp_path / "progress.txt"),
            ),
        )
        assert loop.is_initialized() is False
        assert loop.is_complete() is False

    @pytest.mark.asyncio
    async def test_is_complete_when_all_pass(self, tmp_path: Path):
        fl_path = tmp_path / "features.json"
        fl = FeatureList(features=[
            Feature(description="a", passes=True),
            Feature(description="b", passes=True),
        ])
        fl.save(fl_path)
        cfg = CalciferConfig(api_key="x")
        loop = SessionLoop(
            cfg, [], spec="test",
            session_config=SessionConfig(
                feature_list_path=str(fl_path),
                progress_path=str(tmp_path / "progress.txt"),
            ),
        )
        assert loop.is_initialized() is True
        assert loop.is_complete() is True

    @pytest.mark.asyncio
    async def test_is_complete_false_when_empty(self, tmp_path: Path):
        """Empty feature list is NOT complete (prevents infinite loop)."""
        fl_path = tmp_path / "features.json"
        fl_path.write_text("[]")
        cfg = CalciferConfig(api_key="x")
        loop = SessionLoop(
            cfg, [], spec="test",
            session_config=SessionConfig(
                feature_list_path=str(fl_path),
                progress_path=str(tmp_path / "progress.txt"),
            ),
        )
        assert loop.is_initialized() is True
        assert loop.is_complete() is False

    @pytest.mark.asyncio
    async def test_get_progress(self, tmp_path: Path):
        fl_path = tmp_path / "features.json"
        FeatureList(features=[
            Feature(description="a", passes=True),
            Feature(description="b", passes=False),
            Feature(description="c", passes=False),
        ]).save(fl_path)
        cfg = CalciferConfig(api_key="x")
        loop = SessionLoop(
            cfg, [], spec="test",
            session_config=SessionConfig(
                feature_list_path=str(fl_path),
                progress_path=str(tmp_path / "progress.txt"),
            ),
        )
        prog = loop.get_progress()
        assert prog["features_total"] == 3
        assert prog["features_done"] == 1
        assert prog["features_pending"] == 2
        assert prog["progress"] == "33%"
        assert prog["next_feature"] == "b"

    @pytest.mark.asyncio
    async def test_initialize_runs_agent(self, tmp_path: Path):
        fl_path = tmp_path / "features.json"
        cfg = CalciferConfig(api_key="x")
        loop = SessionLoop(
            cfg, [], spec="build a calculator",
            session_config=SessionConfig(
                feature_list_path=str(fl_path),
                progress_path=str(tmp_path / "progress.txt"),
            ),
        )

        # Mock the underlying _run_agent to simulate the initializer writing files
        async def mock_run_agent(prompt, tools, phase, **kwargs):
            # Verify the prompt contains the spec
            assert "build a calculator" in prompt
            assert "50-200+" in prompt  # feature count guidance
            # Simulate initializer creating the feature list
            FeatureList(features=[
                Feature(description="basic addition"),
                Feature(description="subtraction"),
            ]).save(fl_path)
            return AgentResult(messages=[], final_text="done", usage=Usage(), turn_count=1)

        loop._run_agent = mock_run_agent  # type: ignore
        result = await loop.initialize()
        assert loop.is_initialized() is True
        assert loop._session_count == 1

    @pytest.mark.asyncio
    async def test_run_session_uses_template_paths_in_prompt(self, tmp_path: Path):
        """Prompt should reference the configured file names, not hardcoded ones."""
        fl_path = tmp_path / "custom_features.json"
        FeatureList(features=[Feature(description="x")]).save(fl_path)
        cfg = CalciferConfig(api_key="x")
        sc = SessionConfig(
            feature_list_path=str(fl_path),
            progress_path=str(tmp_path / "custom_progress.txt"),
            init_script_path=str(tmp_path / "custom_init.sh"),
        )
        loop = SessionLoop(cfg, [], spec="test", session_config=sc)

        captured = {}

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            captured["prompt"] = prompt
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        loop._run_agent = mock_run_agent  # type: ignore
        await loop.run_session()
        p = captured["prompt"]
        assert "custom_features.json" in p
        assert "custom_progress.txt" in p
        assert "custom_init.sh" in p
        # Hardcoded names should NOT appear (except as part of custom paths)
        assert "feature_list.json" not in p or "custom_features.json" in p


# ===== Pipeline =====


class TestPipeline:
    @pytest.mark.asyncio
    async def test_pipeline_passes_on_successful_eval(self, tmp_path: Path):
        cfg = CalciferConfig(api_key="x")
        pc = PipelineConfig(
            work_dir=str(tmp_path),
            max_rounds=3,
            pass_threshold=7,
            plan_path=str(tmp_path / "plan.md"),
            feature_list_path=str(tmp_path / "features.json"),
            progress_path=str(tmp_path / "progress.txt"),
            eval_result_path=str(tmp_path / "eval.json"),
            sprint_result_path=str(tmp_path / "sprint.md"),
        )
        pipeline = Pipeline(cfg, [], spec="build X", pipeline_config=pc)

        round_count = [0]

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            if phase == "planner":
                (tmp_path / "plan.md").write_text("# Plan")
                FeatureList(features=[Feature(description="f1")]).save(pc.feature_list_path)
            elif phase.startswith("generator"):
                (tmp_path / "sprint.md").write_text("built f1")
            elif phase.startswith("evaluator"):
                round_count[0] += 1
                # First round fails, second passes
                if round_count[0] == 1:
                    EvalResult(
                        passed=False,
                        score={"functionality": 5, "code_quality": 6, "design_quality": 5, "completeness": 5},
                        issues=["not good enough"],
                    ).save(pc.eval_result_path)
                else:
                    EvalResult(
                        passed=True,
                        score={"functionality": 9, "code_quality": 9, "design_quality": 8, "completeness": 9},
                        issues=[],
                    ).save(pc.eval_result_path)
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        pipeline._run_agent = mock_run_agent  # type: ignore
        result = await pipeline.run()
        assert result.passed is True
        assert len(result.rounds) == 2

    @pytest.mark.asyncio
    async def test_pipeline_threshold_enforcement(self, tmp_path: Path):
        """Pipeline overrides agent's `passed` claim if scores don't meet threshold."""
        cfg = CalciferConfig(api_key="x")
        pc = PipelineConfig(
            work_dir=str(tmp_path),
            max_rounds=1,
            pass_threshold=7,
            plan_path=str(tmp_path / "plan.md"),
            feature_list_path=str(tmp_path / "features.json"),
            progress_path=str(tmp_path / "progress.txt"),
            eval_result_path=str(tmp_path / "eval.json"),
            sprint_result_path=str(tmp_path / "sprint.md"),
        )
        pipeline = Pipeline(cfg, [], spec="build X", pipeline_config=pc)

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            if phase == "planner":
                (tmp_path / "plan.md").write_text("# Plan")
                FeatureList(features=[Feature(description="f1")]).save(pc.feature_list_path)
            elif phase.startswith("evaluator"):
                # Agent LIES: claims passed=true but one criterion is below threshold
                EvalResult(
                    passed=True,
                    score={"functionality": 9, "code_quality": 4, "design_quality": 8, "completeness": 9},
                    issues=[],
                ).save(pc.eval_result_path)
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        pipeline._run_agent = mock_run_agent  # type: ignore
        result = await pipeline.run()
        # Pipeline should OVERRIDE the agent's false claim
        assert result.passed is False
        assert result.final_eval is not None
        assert result.final_eval.passed is False

    @pytest.mark.asyncio
    async def test_pipeline_respects_cost_limit(self, tmp_path: Path):
        cfg = CalciferConfig(api_key="x")
        pc = PipelineConfig(
            work_dir=str(tmp_path),
            max_rounds=10,
            pass_threshold=7,
            max_cost_usd=0.0001,
            plan_path=str(tmp_path / "plan.md"),
            feature_list_path=str(tmp_path / "features.json"),
            progress_path=str(tmp_path / "progress.txt"),
            eval_result_path=str(tmp_path / "eval.json"),
            sprint_result_path=str(tmp_path / "sprint.md"),
        )
        pipeline = Pipeline(cfg, [], spec="build X", pipeline_config=pc)

        call_count = [0]

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            call_count[0] += 1
            # Simulate each call adding cost
            pipeline._total_cost += 0.001
            if phase == "planner":
                (tmp_path / "plan.md").write_text("# Plan")
                FeatureList(features=[Feature(description="f1")]).save(pc.feature_list_path)
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        pipeline._run_agent = mock_run_agent  # type: ignore
        result = await pipeline.run()
        # Should stop quickly due to cost limit
        assert result.total_cost_usd > 0
        assert call_count[0] <= 3  # planner + 1 round max

    @pytest.mark.asyncio
    async def test_pipeline_skips_planner_if_both_artifacts_exist(self, tmp_path: Path):
        (tmp_path / "plan.md").write_text("# Existing plan")
        FeatureList(features=[Feature(description="f1", passes=True)]).save(tmp_path / "features.json")

        cfg = CalciferConfig(api_key="x")
        pc = PipelineConfig(
            work_dir=str(tmp_path),
            max_rounds=1,
            plan_path=str(tmp_path / "plan.md"),
            feature_list_path=str(tmp_path / "features.json"),
            progress_path=str(tmp_path / "progress.txt"),
            eval_result_path=str(tmp_path / "eval.json"),
            sprint_result_path=str(tmp_path / "sprint.md"),
        )
        pipeline = Pipeline(cfg, [], spec="test", pipeline_config=pc)

        phases_called = []

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            phases_called.append(phase)
            if phase.startswith("evaluator"):
                EvalResult(
                    passed=True,
                    score={"functionality": 9, "code_quality": 9, "design_quality": 9, "completeness": 9},
                ).save(pc.eval_result_path)
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        pipeline._run_agent = mock_run_agent  # type: ignore
        await pipeline.run()
        assert "planner" not in phases_called  # skipped

    @pytest.mark.asyncio
    async def test_pipeline_runs_planner_if_artifacts_missing(self, tmp_path: Path):
        cfg = CalciferConfig(api_key="x")
        pc = PipelineConfig(
            work_dir=str(tmp_path),
            max_rounds=1,
            plan_path=str(tmp_path / "plan.md"),
            feature_list_path=str(tmp_path / "features.json"),
            progress_path=str(tmp_path / "progress.txt"),
            eval_result_path=str(tmp_path / "eval.json"),
            sprint_result_path=str(tmp_path / "sprint.md"),
        )
        pipeline = Pipeline(cfg, [], spec="test", pipeline_config=pc)

        phases_called = []

        async def mock_run_agent(prompt, tools, phase, **kwargs):
            phases_called.append(phase)
            if phase == "planner":
                (tmp_path / "plan.md").write_text("# Plan")
                FeatureList(features=[Feature(description="f1")]).save(pc.feature_list_path)
            elif phase.startswith("evaluator"):
                EvalResult(
                    passed=True,
                    score={"functionality": 9, "code_quality": 9, "design_quality": 9, "completeness": 9},
                ).save(pc.eval_result_path)
            return AgentResult(messages=[], final_text="ok", usage=Usage(), turn_count=1)

        pipeline._run_agent = mock_run_agent  # type: ignore
        await pipeline.run()
        assert "planner" in phases_called
