from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Make the project root importable regardless of how this script is invoked,
# so the generic-manager vocabulary is read from build_balanced_events.py
# (the single source of truth) instead of a second, driftable copy here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.build_balanced_events import GENERIC_MANAGER_NAMES  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", default="data")
    args = p.parse_args()
    root = Path(args.data_root)
    event_path = root / "derived" / "balanced_allocation_events.csv"
    market_path = root / "derived" / "market_70_30_proxy_monthly.csv"
    fund_path = root / "derived" / "balanced_portfolio_month_returns.csv"

    e = pd.read_csv(event_path, low_memory=False)
    m = pd.read_csv(market_path, low_memory=False)
    f = pd.read_csv(fund_path, low_memory=False)

    for c in ["month_end", "report_date_original", "prev_month_end"]:
        if c in e.columns:
            e[c] = pd.to_datetime(e[c], errors="coerce")
    m["month_end"] = pd.to_datetime(m["month_end"], errors="coerce")
    f["month_end"] = pd.to_datetime(f["month_end"], errors="coerce")

    checks: dict[str, object] = {}
    required: list[str] = []  # keys that must be truthy for overall PASS

    def require(name: str, value: bool) -> None:
        checks[name] = bool(value)
        required.append(name)

    # ---------------- Identity / uniqueness ----------------
    require("event_id_unique", e["event_id"].is_unique)
    require("portfolio_month_unique", not e.duplicated(subset=["crsp_portno", "month_end"]).any())
    require(
        "month_end_is_calendar_month_end",
        (e["month_end"] == e["month_end"].dt.to_period("M").dt.to_timestamp("M")).all(),
    )
    require("original_report_not_after_month_end", (e["report_date_original"] <= e["month_end"]).all())

    # ---------------- Calendar ----------------
    mm = m.sort_values("month_end")
    m_month_idx = mm["month_end"].dt.year * 12 + mm["month_end"].dt.month
    checks["market_monthly_gap_count"] = int((m_month_idx.diff().dropna() != 1).sum())
    require("market_monthly_sequence_complete", checks["market_monthly_gap_count"] == 0)
    require("market_month_end_unique", not mm["month_end"].duplicated().any())

    # Fund-report reporting gaps are expected (funds don't report every month) --
    # this is a distribution report, not a pass/fail gate.
    fund_gaps: list[float] = []
    for _, g in f.sort_values("month_end").groupby("crsp_portno"):
        months = g["month_end"].dt.year * 12 + g["month_end"].dt.month
        fund_gaps.extend((months.diff().dropna() - 1).clip(lower=0).tolist())
    checks["fund_return_gap_month_distribution"] = {
        "portfolios": int(f["crsp_portno"].nunique()),
        "median_gap_months_between_consecutive_reports": float(np.median(fund_gaps)) if fund_gaps else 0.0,
        "max_gap_months_between_consecutive_reports": float(np.max(fund_gaps)) if fund_gaps else 0.0,
    }

    # ---------------- Manager fallback ----------------
    generic = e["manager_raw"].fillna("").astype(str).str.strip().str.lower().isin(GENERIC_MANAGER_NAMES)
    require(
        "generic_manager_never_labeled_manager_level",
        not (generic & e["analysis_entity_level"].eq("manager")).any(),
    )
    checks["analysis_entity_level_counts"] = e["analysis_entity_level"].value_counts(dropna=False).to_dict()
    unresolved = e["analysis_entity_level"].eq("unresolved")
    require(
        "unresolved_rows_use_unresolved_label",
        e.loc[unresolved, "analysis_entity"].eq("UNRESOLVED MANAGER/FAMILY").all(),
    )
    require(
        "analysis_entity_level_values_known",
        e["analysis_entity_level"].isin(["manager", "advisor_team_fallback", "family_fallback", "unresolved"]).all(),
    )

    # ---------------- Allocation ----------------
    for col in ["stock_weight", "bond_weight", "cash_weight"]:
        s = pd.to_numeric(e[col], errors="coerce")
        checks[f"{col}_out_of_sane_range_count"] = int(((s < -0.10) | (s > 1.20)).sum())
    require(
        "allocation_components_within_sane_range",
        all(checks[f"{c}_out_of_sane_range_count"] == 0 for c in ["stock_weight", "bond_weight", "cash_weight"]),
    )

    implied = e["bond_imputed_from_residual"].fillna(False).astype(bool)
    if implied.any():
        g = e[implied & e["allocation_source"].astype(str).str.startswith("reported_summary")]
        err = (g["stock_weight"] + g["bond_weight"] + g["cash_weight"] - 1).abs()
        checks["bond_residual_max_abs_error"] = float(err.max()) if not g.empty else None
        require("bond_residual_pass", g.empty or err.max() < 1e-9)
    else:
        checks["bond_residual_pass"] = True

    known_total = e[["stock_weight", "bond_weight", "cash_weight"]].sum(axis=1, min_count=3)
    recomputed_other = 1.0 - known_total
    mask = e["other_unclassified_weight"].notna() & recomputed_other.notna()
    err = (e.loc[mask, "other_unclassified_weight"] - recomputed_other[mask]).abs()
    checks["other_unclassified_weight_max_abs_error"] = float(err.max()) if mask.any() else None
    require("other_unclassified_weight_exact", not mask.any() or err.max() < 1e-9)
    checks["other_unclassified_weight_negative_count"] = int((e["other_unclassified_weight"] < -1e-9).sum())

    checks["allocation_quality_counts"] = e["allocation_quality"].value_counts(dropna=False).to_dict()
    checks["allocation_source_counts"] = e["allocation_source"].value_counts(dropna=False).to_dict()

    # Diagnostics only (research-inclusion questions, not correctness failures):
    # does a "comparable" adjacent-report change mix differing allocation source/quality?
    e_sorted = e.sort_values(["crsp_portno", "month_end"])
    comparable = e_sorted["comparable_change"].fillna(False).astype(bool)
    prev_source = e_sorted.groupby("crsp_portno")["allocation_source"].shift(1)
    prev_quality = e_sorted.groupby("crsp_portno")["allocation_quality"].shift(1)
    checks["comparable_change_with_differing_allocation_source"] = int(
        (comparable & e_sorted["allocation_source"].ne(prev_source)).sum()
    )
    checks["comparable_change_with_differing_allocation_quality"] = int(
        (comparable & e_sorted["allocation_quality"].ne(prev_quality)).sum()
    )
    checks["comparable_change_with_non_high_quality_current_or_prev"] = int(
        (comparable & (~e_sorted["allocation_quality"].eq("high") | ~prev_quality.eq("high"))).sum()
    )
    checks["note_comparable_change_definition"] = (
        "comparable_change requires only stock_weight/prev_stock_weight and a 1..N month gap; "
        "it does not itself require bond_weight/cash_weight or a minimum allocation_quality on "
        "either side of the comparison. See CLAUDE_STEP1_REVIEW_AND_CHANGELOG.md for details."
    )

    # ---------------- Change ----------------
    key_pairs = set(zip(e["crsp_portno"].tolist(), e["month_end"].tolist()))
    prev_pairs = list(zip(e_sorted["crsp_portno"].tolist(), e_sorted["prev_month_end"].tolist()))
    missing_prev = sum(1 for portno, pm in prev_pairs if pd.notna(pm) and (portno, pm) not in key_pairs)
    checks["prev_month_end_missing_as_own_event_count"] = int(missing_prev)
    require("prev_month_end_always_a_real_prior_event", missing_prev == 0)

    gap_recalc = (e_sorted["month_end"].dt.year * 12 + e_sorted["month_end"].dt.month) - (
        e_sorted["prev_month_end"].dt.year * 12 + e_sorted["prev_month_end"].dt.month
    )
    gmask = e_sorted["gap_months_from_prev_report"].notna() & gap_recalc.notna()
    gap_err = (e_sorted.loc[gmask, "gap_months_from_prev_report"] - gap_recalc[gmask]).abs()
    checks["gap_months_from_prev_report_max_abs_error"] = float(gap_err.max()) if gmask.any() else None
    require("gap_months_from_prev_report_exact", not gmask.any() or gap_err.max() < 1e-9)

    dcols = ["delta_stock_pp", "delta_bond_pp", "delta_cash_pp"]
    for comp, col in zip(["stock", "bond", "cash"], dcols):
        cur = pd.to_numeric(e_sorted[f"{comp}_weight"], errors="coerce")
        prev = pd.to_numeric(e_sorted[f"prev_{comp}_weight"], errors="coerce")
        recomputed = (cur - prev) * 100.0
        mask = e_sorted[col].notna() & recomputed.notna()
        err = (e_sorted.loc[mask, col] - recomputed[mask]).abs()
        checks[f"{col}_max_abs_error"] = float(err.max()) if mask.any() else None
        require(f"{col}_exact", not mask.any() or err.max() < 1e-9)

    l1_recalc = e_sorted[dcols].abs().sum(axis=1, min_count=1)
    mask = e_sorted["allocation_change_l1_pp"].notna() & l1_recalc.notna()
    err = (e_sorted.loc[mask, "allocation_change_l1_pp"] - l1_recalc[mask]).abs()
    checks["allocation_change_l1_pp_max_abs_error"] = float(err.max()) if mask.any() else None
    require("allocation_change_l1_pp_exact", not mask.any() or err.max() < 1e-9)
    checks["allocation_change_l1_pp_partial_component_count"] = int(
        (e_sorted[dcols].isna().any(axis=1) & e_sorted[dcols].notna().any(axis=1)).sum()
    )
    checks["note_l1_partial_components"] = (
        "allocation_change_l1_pp uses sum(min_count=1): when only some of "
        "delta_stock_pp/delta_bond_pp/delta_cash_pp are non-null, the missing components "
        "contribute 0 rather than making L1 NaN. The count above is how many rows this affects."
    )

    turnover_recalc = 0.5 * e_sorted["allocation_change_l1_pp"]
    mask = e_sorted["allocation_turnover_pp"].notna() & turnover_recalc.notna()
    err = (e_sorted.loc[mask, "allocation_turnover_pp"] - turnover_recalc[mask]).abs()
    require("allocation_turnover_pp_exact", not mask.any() or err.max() < 1e-9)

    checks["has_allocation_change_with_partial_components_count"] = int(
        (e_sorted["has_allocation_change"].fillna(False) & e_sorted[dcols].isna().any(axis=1)).sum()
    )

    # ---------------- Fund return ----------------
    has_exp = e["expense_ratio_annual"].notna()
    gross_minus_net = e["fund_gross_return_approx"] - e["fund_net_return"]
    expected_when_exp = pd.to_numeric(e["expense_ratio_annual"], errors="coerce") / 12.0
    mask = has_exp & gross_minus_net.notna()
    err = (gross_minus_net[mask] - expected_when_exp[mask]).abs()
    require("gross_return_formula_exact_when_expense_known", not mask.any() or err.max() < 1e-9)
    mask0 = (~has_exp) & gross_minus_net.notna()
    require(
        "gross_equals_net_when_expense_missing",
        not mask0.any() or (gross_minus_net[mask0].abs() < 1e-9).all(),
    )
    checks["expense_ratio_missing_count"] = int((~has_exp).sum())
    checks["expense_ratio_missing_pct"] = float((~has_exp).mean())
    require("fund_return_csv_unique_by_portfolio_month", not f.duplicated(subset=["crsp_portno", "month_end"]).any())

    # ---------------- Benchmark ----------------
    spxt_recalc = m["spxt_index_level"].pct_change()
    mask = m["spxt_total_return"].notna() & spxt_recalc.notna()
    err = (m.loc[mask, "spxt_total_return"] - spxt_recalc[mask]).abs()
    require("spxt_total_return_matches_index_pct_change", not mask.any() or err.max() < 1e-9)

    prev_y = (m["treasury10y_yield_pct"] / 100.0).shift(1)
    dy = (m["treasury10y_yield_pct"] / 100.0).diff()
    for label, d in [("d7p5", 7.5), ("d8p5", 8.5), ("d9p5", 9.5)]:
        col = f"treasury10y_proxy_return_{label}"
        if col in m.columns:
            recalc = prev_y / 12.0 - d * dy
            mask = m[col].notna() & recalc.notna()
            err = (m.loc[mask, col] - recalc[mask]).abs()
            require(f"{col}_formula_exact", not mask.any() or err.max() < 1e-9)

    combo_recalc = 0.70 * m["spxt_total_return"] + 0.30 * m["treasury10y_proxy_return"]
    mask = m["benchmark_70_30_return"].notna() & combo_recalc.notna()
    err = (m.loc[mask, "benchmark_70_30_return"] - combo_recalc[mask]).abs()
    require("benchmark_70_30_formula_exact", not mask.any() or err.max() < 1e-9)
    require(
        "benchmark_method_label_present_and_proxy_disclosed",
        m["benchmark_method"].notna().all() and m["benchmark_method"].astype(str).str.contains("proxy").all(),
    )

    # ---------------- Future 6M ----------------
    def check_forward_6m(df: pd.DataFrame, value_col: str, target_col: str, group_col: str | None) -> list[float]:
        errors: list[float] = []
        groups = df.groupby(group_col) if group_col else [(None, df)]
        for _, g in groups:
            g = g.sort_values("month_end")
            r = pd.to_numeric(g[value_col], errors="coerce").to_numpy(dtype=float)
            tgt = pd.to_numeric(g[target_col], errors="coerce").to_numpy(dtype=float)
            for i in range(len(g)):
                w = r[i + 1 : i + 7]
                if len(w) == 6 and np.isfinite(w).all() and np.isfinite(tgt[i]):
                    errors.append(abs((np.prod(1 + w) - 1) - tgt[i]))
        return errors

    errors = check_forward_6m(m, "benchmark_70_30_return", "future_6m_benchmark_return", None)
    checks["future_6m_benchmark_max_abs_error"] = float(max(errors)) if errors else None
    checks["future_6m_benchmark_checked_n"] = len(errors)
    require("future_6m_benchmark_pass", not errors or max(errors) < 1e-9)

    errors = check_forward_6m(f, "fund_gross_return_approx", "future_6m_fund_gross_return", "crsp_portno")
    checks["future_6m_fund_gross_max_abs_error"] = float(max(errors)) if errors else None
    checks["future_6m_fund_gross_checked_n"] = len(errors)
    require("future_6m_fund_gross_pass", not errors or max(errors) < 1e-9)

    excess_recalc = e["future_6m_fund_gross_return"] - e["future_6m_benchmark_return"]
    mask = e["future_6m_excess_vs_70_30"].notna() & excess_recalc.notna()
    err = (e.loc[mask, "future_6m_excess_vs_70_30"] - excess_recalc[mask]).abs()
    require("future_6m_excess_exact", not mask.any() or err.max() < 1e-9)

    last_calendar_month = m["month_end"].max()
    tail = e[e["month_end"] > last_calendar_month - pd.DateOffset(months=6)]
    checks["events_in_last_6_calendar_months"] = int(len(tail))
    require("last_6_months_future_6m_unavailable", tail["future_6m_benchmark_return"].isna().all())

    # ---------------- Merge coverage (reported, not pass/fail) ----------------
    checks["merge_coverage"] = {
        "holdings_events_matched_to_fund_returns_pct": float(e["fund_net_return"].notna().mean()),
        "holdings_events_matched_to_market_pct": float(e["benchmark_70_30_return"].notna().mean()),
        "allocation_available_pct": float(e["stock_weight"].notna().mean()),
        "eligible_future_6m_pct": float(e["event_eligible_6m"].fillna(False).astype(bool).mean()),
        "manager_resolved_pct": float(e["analysis_entity_level"].eq("manager").mean()),
        "advisor_team_fallback_pct": float(e["analysis_entity_level"].eq("advisor_team_fallback").mean()),
        "family_fallback_pct": float(e["analysis_entity_level"].eq("family_fallback").mean()),
        "unresolved_pct": float(e["analysis_entity_level"].eq("unresolved").mean()),
    }

    checks["event_rows"] = int(len(e))
    checks["date_min"] = str(e["month_end"].min().date())
    checks["date_max"] = str(e["month_end"].max().date())

    checks["PASS"] = bool(all(checks[k] for k in required))
    checks["_required_checks"] = required

    out_path = root / "derived" / "STEP1_VALIDATION.json"
    out_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
