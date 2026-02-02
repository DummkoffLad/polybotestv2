"""Validation report generator for go/no-go live trading decisions."""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List
from datetime import datetime


@dataclass
class GoNoGoDecision:
    """Go/no-go decision for live trading readiness.

    Attributes:
        decision: True = GO, False = NO-GO
        criteria_results: Dict mapping each criterion name to pass/fail
        rationale: Human-readable explanation for each criterion
        overall_confidence: "low", "medium", or "high" based on session count
    """
    decision: bool
    criteria_results: Dict[str, bool]
    rationale: str
    overall_confidence: str


@dataclass
class ValidationSummary:
    """Summary of validation test results.

    Attributes:
        oos_sessions_tested: Number of out-of-sample sessions tested
        oos_mean_pnl: Mean PnL across all out-of-sample sessions
        oos_ci_lower: 95% confidence interval lower bound
        oos_ci_upper: 95% confidence interval upper bound
        oos_positive_sessions: Number of sessions with positive PnL
        param_variants_tested: Number of parameter variants tested
        params_fragile: List of fragile parameter names
        params_robust: List of robust parameter names
        latency_degradation_pct: PnL degradation due to latency (percentage)
    """
    oos_sessions_tested: int
    oos_mean_pnl: Decimal
    oos_ci_lower: float
    oos_ci_upper: float
    oos_positive_sessions: int
    param_variants_tested: int
    params_fragile: List[str]
    params_robust: List[str]
    latency_degradation_pct: float


class ValidationReportGenerator:
    """Generates validation reports with go/no-go decisions for live trading."""

    # Critical parameters that must be robust
    CRITICAL_PARAMS = {"kelly_fraction", "quality_threshold"}

    # Decision thresholds
    DOWNSIDE_THRESHOLD = -5.0  # CI lower bound must be > -$5
    LATENCY_THRESHOLD = 15.0   # Latency degradation must be < 15%

    def __init__(self, output_dir: Path = Path("data/validation")):
        """Initialize report generator.

        Args:
            output_dir: Directory to save reports (default: data/validation)
        """
        self.output_dir = output_dir

    def decide_go_nogo(self, summary: ValidationSummary) -> GoNoGoDecision:
        """Make go/no-go decision based on validation summary.

        All 4 criteria must pass for GO:
        1. positive_mean_pnl: mean PnL > 0
        2. acceptable_downside: 95% CI lower bound > -$5
        3. no_critical_fragile: no fragile params in critical set
        4. acceptable_latency: latency degradation < 15%

        Args:
            summary: ValidationSummary with test results

        Returns:
            GoNoGoDecision with decision, criteria results, and rationale
        """
        # Evaluate each criterion
        criteria_results = {}
        rationale_parts = []

        # Criterion 1: Positive mean PnL
        positive_mean_pnl = summary.oos_mean_pnl > 0
        criteria_results["positive_mean_pnl"] = positive_mean_pnl
        if positive_mean_pnl:
            rationale_parts.append(
                f"[PASS] Positive mean PnL: ${float(summary.oos_mean_pnl):.2f}"
            )
        else:
            rationale_parts.append(
                f"[FAIL] Negative mean PnL: ${float(summary.oos_mean_pnl):.2f}"
            )

        # Criterion 2: Acceptable downside risk
        acceptable_downside = summary.oos_ci_lower > self.DOWNSIDE_THRESHOLD
        criteria_results["acceptable_downside"] = acceptable_downside
        if acceptable_downside:
            rationale_parts.append(
                f"[PASS] Acceptable downside: 95% CI lower bound ${summary.oos_ci_lower:.2f} > ${self.DOWNSIDE_THRESHOLD:.2f}"
            )
        else:
            rationale_parts.append(
                f"[FAIL] Unacceptable downside: 95% CI lower bound ${summary.oos_ci_lower:.2f} <= ${self.DOWNSIDE_THRESHOLD:.2f}"
            )

        # Criterion 3: No critical parameters are fragile
        fragile_critical = set(summary.params_fragile) & self.CRITICAL_PARAMS
        no_critical_fragile = len(fragile_critical) == 0
        criteria_results["no_critical_fragile"] = no_critical_fragile
        if no_critical_fragile:
            rationale_parts.append(
                f"[PASS] No critical params fragile (tested {summary.param_variants_tested} variants)"
            )
        else:
            rationale_parts.append(
                f"[FAIL] Critical params fragile: {', '.join(fragile_critical)}"
            )

        # Criterion 4: Acceptable latency impact
        acceptable_latency = summary.latency_degradation_pct < self.LATENCY_THRESHOLD
        criteria_results["acceptable_latency"] = acceptable_latency
        if acceptable_latency:
            rationale_parts.append(
                f"[PASS] Acceptable latency: {summary.latency_degradation_pct:.1f}% < {self.LATENCY_THRESHOLD:.0f}%"
            )
        else:
            rationale_parts.append(
                f"[FAIL] Excessive latency: {summary.latency_degradation_pct:.1f}% >= {self.LATENCY_THRESHOLD:.0f}%"
            )

        # Overall decision: ALL must pass for GO
        decision = all(criteria_results.values())

        # Determine confidence level based on session count
        if summary.oos_sessions_tested <= 2:
            confidence = "low"
        elif summary.oos_sessions_tested <= 4:
            confidence = "medium"
        else:
            confidence = "high"

        # Build rationale string
        rationale = "\n".join(rationale_parts)

        return GoNoGoDecision(
            decision=decision,
            criteria_results=criteria_results,
            rationale=rationale,
            overall_confidence=confidence,
        )

    def generate_console_summary(self, summary: ValidationSummary) -> str:
        """Generate concise console summary (under 20 lines).

        Args:
            summary: ValidationSummary with test results

        Returns:
            Formatted console summary string
        """
        decision = self.decide_go_nogo(summary)

        # Header
        lines = [
            "=" * 60,
            "VALIDATION REPORT: GO / NO-GO",
            "=" * 60,
        ]

        # Decision and confidence
        decision_str = "GO" if decision.decision else "NO-GO"
        lines.append(f"Decision: {decision_str}")
        lines.append(f"Confidence: {decision.overall_confidence}")
        lines.append("")

        # Key metrics
        lines.append(
            f"Out-of-Sample: {summary.oos_sessions_tested} sessions, "
            f"mean PnL ${float(summary.oos_mean_pnl):.2f}, "
            f"95% CI [${summary.oos_ci_lower:.2f}, ${summary.oos_ci_upper:.2f}]"
        )
        lines.append(
            f"Sensitivity: {summary.param_variants_tested} params tested, "
            f"{len(summary.params_fragile)} fragile"
        )
        lines.append(
            f"Latency: {summary.latency_degradation_pct:.1f}% degradation (baseline 3.5s)"
        )
        lines.append("")

        # Criteria
        lines.append("Criteria:")
        for criterion, passed in decision.criteria_results.items():
            status = "PASS" if passed else "FAIL"
            lines.append(f"  [{status}] {criterion.replace('_', ' ').title()}")

        lines.append("=" * 60)

        return "\n".join(lines)

    def generate_markdown_report(self, summary: ValidationSummary) -> str:
        """Generate detailed markdown report.

        Args:
            summary: ValidationSummary with test results

        Returns:
            Formatted markdown report string
        """
        decision = self.decide_go_nogo(summary)

        lines = [
            "# Validation Report: Live Trading Readiness",
            "",
            "## Executive Summary",
            "",
            f"**Decision:** {'GO [PASS]' if decision.decision else 'NO-GO [FAIL]'}",
            f"**Confidence:** {decision.overall_confidence.upper()}",
            f"**Sessions Tested:** {summary.oos_sessions_tested}",
            f"**Mean PnL:** ${float(summary.oos_mean_pnl):.2f}",
            "",
        ]

        # Out-of-sample performance
        lines.extend([
            "## Out-of-Sample Performance",
            "",
            f"- **Sessions tested:** {summary.oos_sessions_tested}",
            f"- **Mean PnL:** ${float(summary.oos_mean_pnl):.2f}",
            f"- **95% CI:** [${summary.oos_ci_lower:.2f}, ${summary.oos_ci_upper:.2f}]",
            f"- **Positive sessions:** {summary.oos_positive_sessions}/{summary.oos_sessions_tested}",
            f"- **Win rate:** {100.0 * summary.oos_positive_sessions / summary.oos_sessions_tested:.1f}%",
            "",
        ])

        # Parameter sensitivity
        lines.extend([
            "## Parameter Sensitivity Analysis",
            "",
            f"**Variants tested:** {summary.param_variants_tested}",
            "",
            "**Robust parameters:**",
        ])
        for param in summary.params_robust:
            lines.append(f"- {param}")
        lines.append("")

        lines.append("**Fragile parameters:**")
        if summary.params_fragile:
            for param in summary.params_fragile:
                is_critical = param in self.CRITICAL_PARAMS
                marker = " ⚠️ CRITICAL" if is_critical else ""
                lines.append(f"- {param}{marker}")
        else:
            lines.append("- None")
        lines.append("")

        # Latency impact
        lines.extend([
            "## Latency Impact Analysis",
            "",
            f"**Degradation:** {summary.latency_degradation_pct:.1f}%",
            f"**Threshold:** {self.LATENCY_THRESHOLD:.0f}%",
            f"**Status:** {'PASS' if decision.criteria_results['acceptable_latency'] else 'FAIL'}",
            "",
            "Baseline latency: 3.5s (current infrastructure)",
            "",
        ])

        # Detailed criteria assessment
        lines.extend([
            "## Decision Criteria Assessment",
            "",
            decision.rationale,
            "",
        ])

        # Recommendations
        lines.extend([
            "## Recommendations",
            "",
        ])

        if decision.decision:
            lines.extend([
                "**Status:** READY FOR LIVE TRADING [GO]",
                "",
                "All criteria passed. Strategy has demonstrated:",
                "- Positive expected value on out-of-sample data",
                "- Acceptable downside risk (< $5 drawdown)",
                "- Robust critical parameters",
                "- Acceptable latency tolerance",
                "",
                "Proceed with caution and monitor initial live performance closely.",
            ])
        else:
            lines.extend([
                "**Status:** NOT READY FOR LIVE TRADING [NO-GO]",
                "",
                "Address the following issues before going live:",
                "",
            ])

            if not decision.criteria_results["positive_mean_pnl"]:
                lines.append("- **Fix negative PnL:** Strategy is unprofitable on OOS data. Review trade selection and sizing logic.")

            if not decision.criteria_results["acceptable_downside"]:
                lines.append("- **Reduce downside risk:** 95% CI lower bound exceeds -$5 threshold. Reduce position sizing or improve stop-loss logic.")

            if not decision.criteria_results["no_critical_fragile"]:
                fragile_critical = set(summary.params_fragile) & self.CRITICAL_PARAMS
                lines.append(f"- **Fix fragile critical params:** {', '.join(fragile_critical)} are sensitive to small changes. Find more robust values or simplify logic.")

            if not decision.criteria_results["acceptable_latency"]:
                lines.append(f"- **Reduce latency impact:** {summary.latency_degradation_pct:.1f}% degradation exceeds {self.LATENCY_THRESHOLD:.0f}% threshold. Optimize execution speed or adjust strategy to be less time-sensitive.")

        lines.extend([
            "",
            "---",
            f"*Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        ])

        return "\n".join(lines)

    def save_report(
        self, summary: ValidationSummary, filename: str = None
    ) -> Path:
        """Save markdown report to file.

        Args:
            summary: ValidationSummary with test results
            filename: Optional filename (default: timestamped)

        Returns:
            Path to saved report file
        """
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename if not provided
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"validation_report_{timestamp}.md"

        # Generate report content
        report_content = self.generate_markdown_report(summary)

        # Write to file with UTF-8 encoding (Windows default cp1252 doesn't support checkmarks)
        report_path = self.output_dir / filename
        report_path.write_text(report_content, encoding='utf-8')

        return report_path
