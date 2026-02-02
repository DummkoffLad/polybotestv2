"""Integration tests for validation pipeline end-to-end workflow."""

import pytest
from decimal import Decimal
from pathlib import Path
from src.validation import (
    ValidationPipeline,
    ValidationSummary,
    DataSplitManager,
    SensitivityResult,
    LatencyImpactResult,
)


# Use existing session from Phase 1 for testing
TEST_SESSION = "session_20260130_032713"
SESSION_PATH = Path("data/sessions") / f"{TEST_SESSION}.jsonl"


@pytest.fixture
def temp_validation_dir(tmp_path):
    """Create temporary validation directory for tests."""
    return tmp_path / "validation"


@pytest.fixture
def pipeline(temp_validation_dir):
    """Create ValidationPipeline with temp output dir."""
    return ValidationPipeline(
        session_dir=Path("data/sessions"),
        output_dir=temp_validation_dir,
        strategy_name="mirror"
    )


def test_pipeline_setup_data_split(pipeline, temp_validation_dir):
    """Test pipeline can setup data split correctly."""
    # Setup data split
    pipeline.setup_data_split(
        in_sample_ids=["session_fake_in_sample"],
        out_of_sample_ids=[TEST_SESSION]
    )

    # Verify data split is configured
    assert pipeline.data_split.is_out_of_sample(TEST_SESSION)
    assert pipeline.data_split.is_in_sample("session_fake_in_sample")

    # Verify metadata was saved
    metadata_path = temp_validation_dir / "split_metadata.json"
    assert metadata_path.exists()


def test_pipeline_out_of_sample_replay(pipeline):
    """Test pipeline can run out-of-sample replay."""
    # Mark test session as OOS (normally in-sample for testing only)
    pipeline.setup_data_split(
        in_sample_ids=[],
        out_of_sample_ids=[TEST_SESSION]
    )

    # Run out-of-sample
    pnls = pipeline.run_out_of_sample()

    # Verify returns list of Decimal PnLs
    assert isinstance(pnls, list)
    assert len(pnls) == 1
    assert isinstance(pnls[0], Decimal)
    # PnL should be non-zero (strategy should execute trades)
    # Note: Could be positive or negative


def test_pipeline_sensitivity_sweep(pipeline):
    """Test pipeline can run sensitivity sweep."""
    # Run sensitivity on test session
    result = pipeline.run_sensitivity(SESSION_PATH)

    # Verify returns SensitivityResult
    assert isinstance(result, SensitivityResult)

    # Verify has param_results populated
    assert len(result.param_results) > 0

    # Verify fragile/robust params lists are populated
    assert isinstance(result.fragile_params, list)
    assert isinstance(result.robust_params, list)

    # Verify total_configs_tested > 0
    assert result.total_configs_tested > 0

    # Should have tested all numeric params (15 params * 3 variants = 45 configs)
    # Note: Some params might be filtered out if non-numeric
    assert result.total_configs_tested >= 30  # At least 10 params * 3


def test_pipeline_latency_analysis(pipeline):
    """Test pipeline can run latency analysis."""
    # Run latency analysis on test session
    results = pipeline.run_latency_analysis(SESSION_PATH)

    # Verify returns list of LatencyImpactResult
    assert isinstance(results, list)
    assert len(results) == 4  # zero, baseline, stress_2x, stress_3x

    # Verify each result has correct fields
    for result in results:
        assert isinstance(result, LatencyImpactResult)
        assert result.scenario_name in ["zero", "baseline", "stress_2x", "stress_3x"]

    # Find zero-latency scenario
    zero_latency = next(r for r in results if r.scenario_name == "zero")

    # Verify zero-latency has 0% degradation
    assert zero_latency.degradation_pct == 0.0
    assert zero_latency.avg_delay_ms == 0.0

    # Verify latency scenarios have degradation >= 0 (latency can only hurt)
    for result in results:
        if result.scenario_name != "zero":
            # Degradation should be >= 0 (latency hurts PnL)
            # Note: Could be 0 if no trades executed
            assert result.degradation_pct >= 0.0


def test_pipeline_report_generation_go_decision(temp_validation_dir):
    """Test pipeline report generation with GO decision."""
    from src.validation.report import ValidationReportGenerator

    generator = ValidationReportGenerator(output_dir=temp_validation_dir)

    # Build mock ValidationSummary with positive results
    summary = ValidationSummary(
        oos_sessions_tested=5,
        oos_mean_pnl=Decimal("10.50"),
        oos_ci_lower=2.0,
        oos_ci_upper=18.0,
        oos_positive_sessions=4,
        param_variants_tested=45,
        params_fragile=[],
        params_robust=["kelly_fraction", "quality_threshold"],
        latency_degradation_pct=5.0
    )

    # Generate decision
    decision = generator.decide_go_nogo(summary)

    # Should be GO (all criteria pass)
    assert decision.decision is True
    assert all(decision.criteria_results.values())

    # Generate console summary
    console = generator.generate_console_summary(summary)
    assert "GO" in console
    assert "PASS" in console

    # Generate markdown report
    markdown = generator.generate_markdown_report(summary)
    assert "GO [PASS]" in markdown


def test_pipeline_report_generation_nogo_decision(temp_validation_dir):
    """Test pipeline report generation with NO-GO decision."""
    from src.validation.report import ValidationReportGenerator

    generator = ValidationReportGenerator(output_dir=temp_validation_dir)

    # Build mock ValidationSummary with negative PnL (fails criterion 1)
    summary = ValidationSummary(
        oos_sessions_tested=3,
        oos_mean_pnl=Decimal("-5.00"),  # NEGATIVE
        oos_ci_lower=-10.0,
        oos_ci_upper=0.0,
        oos_positive_sessions=1,
        param_variants_tested=45,
        params_fragile=[],
        params_robust=["kelly_fraction", "quality_threshold"],
        latency_degradation_pct=5.0
    )

    # Generate decision
    decision = generator.decide_go_nogo(summary)

    # Should be NO-GO (negative mean PnL)
    assert decision.decision is False
    assert decision.criteria_results["positive_mean_pnl"] is False

    # Generate console summary
    console = generator.generate_console_summary(summary)
    assert "NO-GO" in console
    assert "FAIL" in console

    # Generate markdown report
    markdown = generator.generate_markdown_report(summary)
    assert "NO-GO [FAIL]" in markdown


@pytest.mark.slow
def test_full_validation_produces_report(pipeline, temp_validation_dir):
    """Test full validation pipeline produces report file."""
    # Setup data split (use test session as OOS for testing)
    pipeline.setup_data_split(
        in_sample_ids=["session_fake_in_sample"],
        out_of_sample_ids=[TEST_SESSION]
    )

    # Run full validation
    summary = pipeline.run_full_validation()

    # Verify summary is returned
    assert isinstance(summary, ValidationSummary)
    assert summary.oos_sessions_tested == 1

    # Verify report file was created
    report_files = list(temp_validation_dir.glob("validation_report_*.md"))
    assert len(report_files) == 1

    # Verify report contains GO or NO-GO
    report_text = report_files[0].read_text(encoding='utf-8')
    assert "GO" in report_text or "NO-GO" in report_text


def test_report_contains_confidence_intervals_and_robustness(temp_validation_dir):
    """Test report contains confidence intervals and robustness metrics."""
    from src.validation.report import ValidationReportGenerator

    generator = ValidationReportGenerator(output_dir=temp_validation_dir)

    # Build ValidationSummary with known CI and sensitivity data
    summary = ValidationSummary(
        oos_sessions_tested=5,
        oos_mean_pnl=Decimal("12.00"),
        oos_ci_lower=3.5,  # Known CI values
        oos_ci_upper=20.5,
        oos_positive_sessions=4,
        param_variants_tested=45,
        params_fragile=["cash_reserve_pct", "per_market_cap_pct"],  # Known fragile params
        params_robust=["kelly_fraction", "quality_threshold"],  # Known robust params
        latency_degradation_pct=8.0
    )

    # Generate markdown report
    markdown = generator.generate_markdown_report(summary)

    # Assert report contains confidence interval values
    assert "95% CI" in markdown or "confidence interval" in markdown.lower()
    assert "$3.5" in markdown or "3.5" in markdown  # CI lower bound
    assert "$20.5" in markdown or "20.5" in markdown  # CI upper bound

    # Assert report contains sensitivity/robustness section
    assert "Sensitivity" in markdown or "Robustness" in markdown or "Parameter" in markdown

    # Assert report contains fragile parameter listings
    assert "cash_reserve_pct" in markdown
    assert "per_market_cap_pct" in markdown

    # Assert report contains robust parameter listings
    assert "kelly_fraction" in markdown
    assert "quality_threshold" in markdown

    # Verify roadmap criterion #4: report contains confidence intervals and robustness metrics
    # This test verifies both are present in the report
    assert True  # If we got here, all assertions passed
