"""Tests for normalized FPGA timing summaries."""

import pytest

from gbs.builtin.diamond.passes import DiamondEcp5Pass
from gbs.builtin.gowin.passes import GowinSynthesizePass
from gbs.builtin.ise.passes import IseSynthesizePass
from gbs.builtin.nextpnr.passes import (
    NextpnrEcp5Pass,
    NextpnrIce40Pass,
    NextpnrXilinxPass,
)
from gbs.builtin.quartus.passes import QuartusSynthesizePass
from gbs.builtin.vivado.passes import VivadoSynthesizePass
from gbs.timing_summary import (
    DiamondTimingParser,
    GowinTimingParser,
    IseTimingParser,
    NextpnrTimingParser,
    QuartusTimingParser,
    TimingSummaryHtml,
    VivadoTimingParser,
)


def test_all_pnr_passes_advertise_timing_summary():
    passes = (
        DiamondEcp5Pass,
        GowinSynthesizePass,
        IseSynthesizePass,
        NextpnrEcp5Pass,
        NextpnrIce40Pass,
        NextpnrXilinxPass,
        QuartusSynthesizePass,
        VivadoSynthesizePass,
    )

    assert all("timing-summary" in pass_.output_types for pass_ in passes)


def test_nextpnr_uses_last_post_route_result_per_clock():
    summary = NextpnrTimingParser.parse("""
Info: Max frequency for clock '$glbnet$clk': 190.26 MHz (PASS at 50.00 MHz)
Info: Max frequency for clock '$glbnet$clk': 199.16 MHz (PASS at 50.00 MHz)
""")

    assert summary.status == "pass"
    assert summary.timing_met is True
    assert len(summary.clocks) == 1
    clock = summary.clocks[0]
    assert clock.name == "$glbnet$clk"
    assert clock.target_mhz == 50.0
    assert clock.fmax_mhz == 199.16
    assert clock.margin_mhz == 149.16
    assert clock.fmax_source == "reported"


def test_vivado_correlates_clock_and_intra_clock_slack():
    summary = VivadoTimingParser.parse("""
All user specified timing constraints are met.
| Clock Summary
Clock  Waveform(ns)       Period(ns)      Frequency(MHz)
-----  ------------       ----------      --------------
clk    {0.000 5.000}      10.000          100.000
| Intra Clock Table
Clock             WNS(ns)      TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints
-----             -------      -------  ---------------------  -------------------
clk                 2.000        0.000                      0                    9
""")

    assert summary.status == "pass"
    clock = summary.clocks[0]
    assert clock.setup_slack_ns == 2.0
    assert clock.fmax_mhz == 125.0
    assert clock.fmax_source == "computed_from_setup_slack"


def test_vivado_does_not_treat_unconstrained_as_pass():
    summary = VivadoTimingParser.parse(
        "There are no user specified timing constraints.\n"
    )

    assert summary.status == "unconstrained"
    assert summary.timing_met is None
    assert summary.clocks == []


def test_diamond_parses_preference_target_and_fmax():
    summary = DiamondTimingParser.parse("""
Preference                              |   Constraint|       Actual|Levels
FREQUENCY NET "clk" 62.000000 MHz ;     |   62.000 MHz|  150.353 MHz|  12
All preferences were met.
""")

    assert summary.status == "pass"
    clock = summary.clocks[0]
    assert clock.name == "clk"
    assert clock.target_mhz == 62.0
    assert clock.fmax_mhz == 150.353
    assert clock.status == "pass"


def test_quartus_uses_worst_corner_and_clock_target():
    summary = QuartusTimingParser.parse("""
; Clocks ;
; Clock Name ; Clock Period ; Frequency ;
; clk ; 10.000 ns ; 100.000 MHz ;
; Slow 1200mV 85C Model Fmax Summary ;
; Fmax ; Restricted Fmax ; Clock Name ;
; 140.000 MHz ; 130.000 MHz ; clk ;
; Slow 1200mV 0C Model Fmax Summary ;
; Fmax ; Restricted Fmax ; Clock Name ;
; 125.000 MHz ; 120.000 MHz ; clk ;
Timing requirements were met
""")

    assert summary.status == "pass"
    clock = summary.clocks[0]
    assert clock.target_mhz == 100.0
    assert clock.fmax_mhz == 120.0
    assert clock.corner == "Slow 1200mV 0C Model"
    assert clock.margin_percent == 20.0


def test_quartus_reports_unconstrained_design():
    summary = QuartusTimingParser.parse("""
Critical Warning: Timing requirements not specified
; Clocks ;
No clocks to report.
""")

    assert summary.status == "unconstrained"
    assert summary.timing_met is None


def test_ise_parses_period_constraint_and_reported_fmax():
    summary = IseTimingParser.parse("""
Timing constraint: TS_clk = PERIOD "clk" 10 ns HIGH 50%;
  Slack: 3.000ns (requirement - (data path - clock path skew + uncertainty))
  Maximum frequency is 142.857MHz.
All constraints were met.
""")

    assert summary.status == "pass"
    clock = summary.clocks[0]
    assert clock.name == "TS_clk"
    assert clock.target_mhz == 100.0
    assert clock.fmax_mhz == 142.857
    assert clock.setup_slack_ns == 3.0


def test_gowin_parses_native_clock_and_fmax_summaries():
    summary = GowinTimingParser.parse("""
2.1 STA Tool Run Summary
<Numbers of Setup Violated Endpoints>:0
<Numbers of Hold Violated Endpoints>:0
2.2 Clock Summary
  NO.                        Clock Name                          Type      Period    Frequency
 ===== ====================================================== =========== ======== =============
  1     clk_50                                                 Base        20.000   50.000MHz
  2     clock_s                                                Base        10.000   100.000MHz
  3     main/video_pll/use_plla.inst/CLKOUT1.default_gen_clk   Generated   6.734    148.500MHz
2.3 Max Frequency Summary
  NO.                        Clock Name                         Constraint    Actual Fmax
 ===== ====================================================== ============== ==============
  1     clock_s                                                100.000(MHz)   366.401(MHz)
  2     main/video_pll/use_plla.inst/CLKOUT1.default_gen_clk   148.500(MHz)   197.993(MHz)
No timing paths to get frequency of clk_50!
2.4 Total Negative Slack Summary
""")

    assert summary.status == "pass"
    assert summary.timing_met is True
    assert [clock.name for clock in summary.clocks] == [
        "clk_50",
        "clock_s",
        "main/video_pll/use_plla.inst/CLKOUT1.default_gen_clk",
    ]
    assert summary.clocks[0].target_mhz == 50.0
    assert summary.clocks[0].fmax_mhz is None
    assert summary.clocks[0].status == "unknown"
    assert summary.clocks[1].fmax_mhz == 366.401
    assert summary.clocks[1].margin_mhz == 266.401
    assert summary.clocks[2].setup_slack_ns == pytest.approx(1.683, abs=0.001)


def test_gowin_violation_counts_fail_constraints():
    summary = GowinTimingParser.parse("""
<Numbers of Setup Violated Endpoints>:2
<Numbers of Hold Violated Endpoints>:0
2.2 Clock Summary
  1     sys_clk   Base   10.000   100.000MHz
2.3 Max Frequency Summary
  1     sys_clk   100.000(MHz)   80.000(MHz)
2.4 Total Negative Slack Summary
""")

    assert summary.status == "fail"
    assert summary.timing_met is False
    assert summary.clocks[0].status == "fail"


def test_summary_yaml_renders_as_safe_html(tmp_path):
    path = tmp_path / "timing.yaml"
    path.write_text("""
schema_version: 1
backend: test
status: fail
constraints:
  timing_met: false
clocks:
  - name: <clock>
    status: fail
    target_mhz: 100.0
    fmax_mhz: 80.0
notes:
  - <unsafe>
""")

    html = TimingSummaryHtml.fragment(path)

    assert "&lt;clock&gt;" in html
    assert "&lt;unsafe&gt;" in html
    assert "<clock>" not in html


@pytest.mark.parametrize(
    "parser",
    [
        DiamondTimingParser,
        GowinTimingParser,
        IseTimingParser,
        NextpnrTimingParser,
        QuartusTimingParser,
        VivadoTimingParser,
    ],
)
def test_unrecognized_valid_report_is_unknown(parser):
    summary = parser.parse("A valid report with no recognizable timing section")

    assert summary.status == "unknown"
    assert summary.timing_met is None
