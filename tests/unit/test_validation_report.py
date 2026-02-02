"""Unit tests for validation report generation and go/no-go decision logic."""

from decimal import Decimal
from pathlib import Path
import pytest
import tempfile
import shutil

from src.validation.report import (
    ValidationReportGenerator,
    GoNoGoDecision,
    ValidationSummary,
)


class TestGoNoGoDecision:
    """Test go/no-go decision logic."""

    def test_all_criteria_pass_produces_go_decision(self):
        """All 4 criteria pass -> GO decision."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=2.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=4,
            param_variants_tested=12,
            params_fragile=["max_allocation"],
            params_robust=["kelly_fraction", "quality_threshold", "rebalance_threshold"],
            latency_degradation_pct=8.5,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is True
        assert decision.criteria_results["positive_mean_pnl"] is True
        assert decision.criteria_results["acceptable_downside"] is True
        assert decision.criteria_results["no_critical_fragile"] is True
        assert decision.criteria_results["acceptable_latency"] is True
        assert "PASS" in decision.rationale.upper()
        assert decision.overall_confidence == "high"

    def test_negative_mean_pnl_produces_no_go(self):
        """Negative mean PnL -> NO-GO with correct rationale."""
        summary = ValidationSummary(
            oos_sessions_tested=4,
            oos_mean_pnl=Decimal("-1.20"),
            oos_ci_lower=-3.0,
            oos_ci_upper=0.5,
            oos_positive_sessions=1,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=10.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        assert decision.criteria_results["positive_mean_pnl"] is False
        assert "negative mean PnL" in decision.rationale.lower() or "mean pnl" in decision.rationale.lower()

    def test_ci_lower_below_threshold_produces_no_go(self):
        """95% CI lower bound < -$5 -> NO-GO with correct rationale."""
        summary = ValidationSummary(
            oos_sessions_tested=3,
            oos_mean_pnl=Decimal("2.00"),
            oos_ci_lower=-6.5,  # Below -$5 threshold
            oos_ci_upper=10.0,
            oos_positive_sessions=2,
            param_variants_tested=8,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        assert decision.criteria_results["acceptable_downside"] is False
        assert "downside" in decision.rationale.lower() or "ci" in decision.rationale.lower()

    def test_fragile_critical_param_produces_no_go(self):
        """Fragile kelly_fraction or quality_threshold -> NO-GO."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("4.00"),
            oos_ci_lower=1.0,
            oos_ci_upper=7.0,
            oos_positive_sessions=4,
            param_variants_tested=15,
            params_fragile=["kelly_fraction", "max_allocation"],  # Critical param fragile
            params_robust=["quality_threshold"],
            latency_degradation_pct=10.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        assert decision.criteria_results["no_critical_fragile"] is False
        assert "fragile" in decision.rationale.lower() or "kelly" in decision.rationale.lower()

    def test_quality_threshold_fragile_produces_no_go(self):
        """Fragile quality_threshold -> NO-GO."""
        summary = ValidationSummary(
            oos_sessions_tested=4,
            oos_mean_pnl=Decimal("3.00"),
            oos_ci_lower=0.5,
            oos_ci_upper=5.5,
            oos_positive_sessions=3,
            param_variants_tested=12,
            params_fragile=["quality_threshold"],  # Critical param fragile
            params_robust=["kelly_fraction"],
            latency_degradation_pct=8.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        assert decision.criteria_results["no_critical_fragile"] is False

    def test_excessive_latency_degradation_produces_no_go(self):
        """Latency degradation > 15% -> NO-GO."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("5.00"),
            oos_ci_lower=2.0,
            oos_ci_upper=8.0,
            oos_positive_sessions=5,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=18.5,  # Above 15% threshold
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        assert decision.criteria_results["acceptable_latency"] is False
        assert "latency" in decision.rationale.lower()

    def test_multiple_failures_all_listed_in_rationale(self):
        """Multiple criterion failures -> NO-GO with all failures in rationale."""
        summary = ValidationSummary(
            oos_sessions_tested=3,
            oos_mean_pnl=Decimal("-0.50"),
            oos_ci_lower=-7.0,
            oos_ci_upper=6.0,
            oos_positive_sessions=1,
            param_variants_tested=8,
            params_fragile=["kelly_fraction"],
            params_robust=[],
            latency_degradation_pct=20.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.decision is False
        # Multiple failures should be documented
        assert decision.criteria_results["positive_mean_pnl"] is False
        assert decision.criteria_results["acceptable_downside"] is False
        assert decision.criteria_results["no_critical_fragile"] is False
        assert decision.criteria_results["acceptable_latency"] is False


class TestConfidenceLevels:
    """Test confidence level calculation based on session count."""

    def test_1_session_gives_low_confidence(self):
        """1 session -> low confidence."""
        summary = ValidationSummary(
            oos_sessions_tested=1,
            oos_mean_pnl=Decimal("5.00"),
            oos_ci_lower=5.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=1,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.overall_confidence == "low"

    def test_2_sessions_gives_low_confidence(self):
        """2 sessions -> low confidence."""
        summary = ValidationSummary(
            oos_sessions_tested=2,
            oos_mean_pnl=Decimal("4.00"),
            oos_ci_lower=2.0,
            oos_ci_upper=6.0,
            oos_positive_sessions=2,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.overall_confidence == "low"

    def test_3_sessions_gives_medium_confidence(self):
        """3 sessions -> medium confidence."""
        summary = ValidationSummary(
            oos_sessions_tested=3,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=1.0,
            oos_ci_upper=6.0,
            oos_positive_sessions=2,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.overall_confidence == "medium"

    def test_4_sessions_gives_medium_confidence(self):
        """4 sessions -> medium confidence."""
        summary = ValidationSummary(
            oos_sessions_tested=4,
            oos_mean_pnl=Decimal("3.00"),
            oos_ci_lower=0.5,
            oos_ci_upper=5.5,
            oos_positive_sessions=3,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.overall_confidence == "medium"

    def test_5_sessions_gives_high_confidence(self):
        """5+ sessions -> high confidence."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("4.00"),
            oos_ci_lower=1.0,
            oos_ci_upper=7.0,
            oos_positive_sessions=4,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=5.0,
        )

        generator = ValidationReportGenerator()
        decision = generator.decide_go_nogo(summary)

        assert decision.overall_confidence == "high"


class TestConsoleSummary:
    """Test console summary generation."""

    def test_console_summary_under_20_lines(self):
        """Console summary is concise (under 20 lines)."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=2.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=4,
            param_variants_tested=12,
            params_fragile=["max_allocation"],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=8.5,
        )

        generator = ValidationReportGenerator()
        console_output = generator.generate_console_summary(summary)

        line_count = len(console_output.strip().split("\n"))
        assert line_count <= 20

    def test_console_summary_includes_decision(self):
        """Console summary includes GO or NO-GO decision."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=2.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=4,
            param_variants_tested=12,
            params_fragile=[],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=8.5,
        )

        generator = ValidationReportGenerator()
        console_output = generator.generate_console_summary(summary)

        assert "GO" in console_output or "NO-GO" in console_output

    def test_console_summary_includes_key_metrics(self):
        """Console summary includes all key metrics."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=2.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=4,
            param_variants_tested=12,
            params_fragile=["max_allocation"],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=8.5,
        )

        generator = ValidationReportGenerator()
        console_output = generator.generate_console_summary(summary)

        # Check for key metrics
        assert "3.50" in console_output or "$3.50" in console_output  # Mean PnL
        assert "5" in console_output  # Sessions tested
        assert "8.5" in console_output  # Latency degradation
        assert "PASS" in console_output or "FAIL" in console_output  # Criteria status


class TestMarkdownReport:
    """Test markdown report generation."""

    def test_markdown_report_includes_all_sections(self):
        """Markdown report includes executive summary, OOS performance, sensitivity, latency, criteria."""
        summary = ValidationSummary(
            oos_sessions_tested=5,
            oos_mean_pnl=Decimal("3.50"),
            oos_ci_lower=2.0,
            oos_ci_upper=5.0,
            oos_positive_sessions=4,
            param_variants_tested=12,
            params_fragile=["max_allocation"],
            params_robust=["kelly_fraction", "quality_threshold"],
            latency_degradation_pct=8.5,
        )

        generator = ValidationReportGenerator()
        markdown_output = generator.generate_markdown_report(summary)

        # Check for all required sections
        assert "executive summary" in markdown_output.lower() or "summary" in markdown_output.lower()
        assert "out-of-sample" in markdown_output.lower() or "oos" in markdown_output.lower()
        assert "sensitivity" in markdown_output.lower() or "parameter" in markdown_output.lower()
        assert "latency" in markdown_output.lower()
        assert "criteria" in markdown_output.lower() or "decision" in markdown_output.lower()

    def test_markdown_report_includes_decision_and_confidence(self):
        """Markdown report includes decision and confidence level."""
        summary = ValidationSummary(
            oos_sessions_tested=3,
            oos_mean_pnl=Decimal("2.00"),
            oos_ci_lower=0.5,
            oos_ci_upper=3.5,
            oos_positive_sessions=2,
            param_variants_tested=10,
            params_fragile=[],
            params_robust=["kelly_fraction"],
            latency_degradation_pct=10.0,
        )

        generator = ValidationReportGenerator()
        markdown_output = generator.generate_markdown_report(summary)

        assert "medium" in markdown_output.lower()  # Confidence
        assert "GO" in markdown_output or "NO-GO" in markdown_output


class TestSaveReport:
    """Test report saving functionality."""

    def test_save_report_creates_file(self):
        """save_report creates markdown file at expected path."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            summary = ValidationSummary(
                oos_sessions_tested=5,
                oos_mean_pnl=Decimal("3.50"),
                oos_ci_lower=2.0,
                oos_ci_upper=5.0,
                oos_positive_sessions=4,
                param_variants_tested=12,
                params_fragile=[],
                params_robust=["kelly_fraction", "quality_threshold"],
                latency_degradation_pct=8.5,
            )

            generator = ValidationReportGenerator(output_dir=temp_dir)
            report_path = generator.save_report(summary, filename="test_report.md")

            assert report_path.exists()
            assert report_path.suffix == ".md"
            assert report_path.parent == temp_dir

            # Verify content was written
            content = report_path.read_text()
            assert len(content) > 0
            assert "GO" in content or "NO-GO" in content

        finally:
            shutil.rmtree(temp_dir)

    def test_save_report_creates_output_dir_if_not_exists(self):
        """save_report creates output_dir if it doesn't exist."""
        temp_dir = Path(tempfile.mkdtemp())
        output_dir = temp_dir / "validation_reports"

        try:
            assert not output_dir.exists()

            summary = ValidationSummary(
                oos_sessions_tested=3,
                oos_mean_pnl=Decimal("2.00"),
                oos_ci_lower=0.5,
                oos_ci_upper=3.5,
                oos_positive_sessions=2,
                param_variants_tested=10,
                params_fragile=[],
                params_robust=["kelly_fraction"],
                latency_degradation_pct=10.0,
            )

            generator = ValidationReportGenerator(output_dir=output_dir)
            report_path = generator.save_report(summary)

            assert output_dir.exists()
            assert report_path.exists()

        finally:
            shutil.rmtree(temp_dir)

    def test_save_report_generates_timestamped_filename_if_none_provided(self):
        """save_report generates timestamped filename if none provided."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            summary = ValidationSummary(
                oos_sessions_tested=5,
                oos_mean_pnl=Decimal("3.50"),
                oos_ci_lower=2.0,
                oos_ci_upper=5.0,
                oos_positive_sessions=4,
                param_variants_tested=12,
                params_fragile=[],
                params_robust=["kelly_fraction"],
                latency_degradation_pct=8.5,
            )

            generator = ValidationReportGenerator(output_dir=temp_dir)
            report_path = generator.save_report(summary)  # No filename provided

            assert report_path.exists()
            # Should have a reasonable filename (contains digits for timestamp)
            assert any(c.isdigit() for c in report_path.stem)

        finally:
            shutil.rmtree(temp_dir)
