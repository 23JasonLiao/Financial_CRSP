from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

GENERIC_MANAGER_NAMES = {
    "", "nan", "none", "unknown", "unknown manager", "team managed", "team manager",
    "management team", "multiple managers", "not disclosed", "n/a", "na",
}

@dataclass(frozen=True)
class BuildConfig:
    research_change_threshold_pp: float = 0.50
    change_epsilon_pp: float = 1e-6
    holdings_proxy_min_coverage: float = 0.70
    holdings_proxy_max_coverage: float = 1.20
    treasury_primary_modified_duration: float = 8.50
    treasury_duration_sensitivity_low: float = 7.50
    treasury_duration_sensitivity_high: float = 9.50
    max_change_gap_months: int = 6


def month_end(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.to_period("M").dt.to_timestamp("M")


def normalize_name(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip()


def is_specific_manager_value(v: object) -> bool:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return False
    return str(v).strip().lower() not in GENERIC_MANAGER_NAMES


def compound_forward_by_calendar(group: pd.DataFrame, value_col: str, out_col: str, months: int = 6) -> pd.DataFrame:
    g = group.sort_values("month_end").copy()
    if g.empty:
        g[out_col] = np.nan
        return g
    calendar = pd.date_range(g["month_end"].min(), g["month_end"].max(), freq="ME")
    s = g.set_index("month_end")[value_col].reindex(calendar)
    vals = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        window = vals[i + 1:i + 1 + months]
        if len(window) == months and np.isfinite(window).all():
            out[i] = np.prod(1.0 + window) - 1.0
    m = pd.DataFrame({"month_end": calendar, out_col: out})
    return g.merge(m, on="month_end", how="left")


def _weighted_group_value(df: pd.DataFrame, keys: list[str], value_col: str, weight_col: str, out_name: str) -> pd.DataFrame:
    v = pd.to_numeric(df[value_col], errors="coerce")
    w = pd.to_numeric(df[weight_col], errors="coerce").clip(lower=0)
    valid = v.notna() & w.notna()
    num = (v.where(valid) * w.where(valid)).groupby([df[k] for k in keys]).sum(min_count=1)
    den = w.where(valid).groupby([df[k] for k in keys]).sum(min_count=1)
    out = (num / den.replace(0, np.nan)).rename(out_name).reset_index()
    # Equal-weight fallback where all valid weights are zero/missing.
    mean = v.groupby([df[k] for k in keys]).mean().rename(out_name + "_mean").reset_index()
    out = out.merge(mean, on=keys, how="outer")
    out[out_name] = out[out_name].fillna(out[out_name + "_mean"])
    return out[keys + [out_name]]


def load_fund_level(data_root: Path) -> pd.DataFrame:
    folder = data_root / "crsp" / "fund_level"
    usecols = [
        "crsp_fundno", "crsp_portno", "fund_name", "ticker", "mgmt_name", "mgr_name", "adv_name",
        "caldt", "mret", "mtna", "exp_ratio", "age_tmp", "turn_ratio",
    ]
    frames = [
        pd.read_csv(folder / "balanced_before2010.csv", usecols=usecols, low_memory=False),
        pd.read_csv(folder / "balanced_after2010.csv", usecols=usecols, low_memory=False),
    ]
    df = pd.concat(frames, ignore_index=True)
    df["caldt_original"] = pd.to_datetime(df["caldt"], errors="coerce")
    df["month_end"] = month_end(df["caldt"])
    df = df.dropna(subset=["crsp_portno", "crsp_fundno", "month_end"]).copy()
    df["crsp_portno"] = pd.to_numeric(df["crsp_portno"], errors="coerce").astype("Int64")
    df["crsp_fundno"] = pd.to_numeric(df["crsp_fundno"], errors="coerce").astype("Int64")
    df = df.sort_values(["crsp_fundno", "month_end", "caldt_original"])

    # Lagged TNA only when prior share-class observation is exactly one month earlier.
    prev_me = df.groupby("crsp_fundno")["month_end"].shift(1)
    lag_tna = pd.to_numeric(df.groupby("crsp_fundno")["mtna"].shift(1), errors="coerce")
    gap = df["month_end"].dt.year * 12 + df["month_end"].dt.month - (prev_me.dt.year * 12 + prev_me.dt.month)
    curr_tna = pd.to_numeric(df["mtna"], errors="coerce")
    df["w_tna"] = np.where(gap.eq(1) & lag_tna.gt(0), lag_tna, curr_tna)
    df["w_tna"] = pd.to_numeric(df["w_tna"], errors="coerce").clip(lower=0)

    df["mret"] = pd.to_numeric(df["mret"], errors="coerce")
    df["exp_ratio"] = pd.to_numeric(df["exp_ratio"], errors="coerce")
    df["gross_return_approx_shareclass"] = df["mret"] + df["exp_ratio"].fillna(0.0) / 12.0
    df["expense_available"] = df["exp_ratio"].notna().astype(float)

    keys = ["crsp_portno", "month_end"]
    base = df.groupby(keys, as_index=False).agg(
        share_class_count=("crsp_fundno", "nunique"),
        portfolio_tna=("mtna", lambda x: pd.to_numeric(x, errors="coerce").clip(lower=0).sum(min_count=1)),
    )
    for value_col, out_col in [
        ("mret", "fund_net_return"),
        ("exp_ratio", "expense_ratio_annual"),
        ("gross_return_approx_shareclass", "fund_gross_return_approx"),
        ("age_tmp", "fund_age_years"),
        ("turn_ratio", "turnover_ratio"),
        ("expense_available", "expense_ratio_weight_coverage"),
    ]:
        base = base.merge(_weighted_group_value(df, keys, value_col, "w_tna", out_col), on=keys, how="left")

    # Pick metadata deterministically: prefer a specific manager name, then latest source row.
    df["manager_specific"] = df["mgr_name"].map(is_specific_manager_value).astype(int)
    meta = (
        df.sort_values(keys + ["manager_specific", "caldt_original"], ascending=[True, True, False, False])
        .drop_duplicates(keys)
        [keys + ["fund_name", "ticker", "mgr_name", "mgmt_name", "adv_name"]]
        .rename(columns={"ticker": "fund_ticker", "mgr_name": "manager_raw", "mgmt_name": "family_name", "adv_name": "advisor_name"})
    )
    out = base.merge(meta, on=keys, how="left")

    manager_specific = out["manager_raw"].map(is_specific_manager_value)
    advisor_ok = out["advisor_name"].fillna("").astype(str).str.strip().str.lower().replace("nan", "").ne("")
    family_ok = out["family_name"].fillna("").astype(str).str.strip().str.lower().replace("nan", "").ne("")
    out["analysis_entity"] = np.select(
        [manager_specific, ~manager_specific & advisor_ok, ~manager_specific & ~advisor_ok & family_ok],
        [out["manager_raw"].fillna(""), "TEAM/FAMILY :: " + out["advisor_name"].fillna(""), "FAMILY :: " + out["family_name"].fillna("")],
        default="UNRESOLVED MANAGER/FAMILY",
    )
    out["analysis_entity_level"] = np.select(
        [manager_specific, ~manager_specific & advisor_ok, ~manager_specific & ~advisor_ok & family_ok],
        ["manager", "advisor_team_fallback", "family_fallback"], default="unresolved"
    )

    # Forward 6M for each portfolio; missing calendar months invalidate the target.
    pieces = []
    for _, g in out.groupby("crsp_portno", sort=False):
        g = compound_forward_by_calendar(g, "fund_gross_return_approx", "future_6m_fund_gross_return", 6)
        g = compound_forward_by_calendar(g, "fund_net_return", "future_6m_fund_net_return", 6)
        pieces.append(g)
    return pd.concat(pieces, ignore_index=True).sort_values(keys)


def load_market(data_root: Path, cfg: BuildConfig) -> pd.DataFrame:
    sp = pd.read_csv(data_root / "market" / "spxt_index_1997_2025.csv", usecols=["Date", "PX_LAST"])
    tr = pd.read_csv(data_root / "market" / "treasury_10y_1997_2025.csv", usecols=["Date", "DGS10_Yield"])
    sp["source_date_spxt"] = pd.to_datetime(sp["Date"], errors="coerce")
    sp["month_end"] = month_end(sp["Date"])
    sp["spxt_index_level"] = pd.to_numeric(sp["PX_LAST"], errors="coerce")
    sp = sp.sort_values("source_date_spxt").drop_duplicates("month_end", keep="last")
    sp["spxt_total_return"] = sp["spxt_index_level"].pct_change()

    tr["source_date_treasury"] = pd.to_datetime(tr["Date"], errors="coerce")
    tr["month_end"] = month_end(tr["Date"])
    tr["treasury10y_yield_pct"] = pd.to_numeric(tr["DGS10_Yield"], errors="coerce")
    tr = tr.sort_values("source_date_treasury").drop_duplicates("month_end", keep="last")
    y = tr["treasury10y_yield_pct"] / 100.0
    tr["treasury10y_yield_change_bps"] = tr["treasury10y_yield_pct"].diff() * 100.0
    prev_y, dy = y.shift(1), y.diff()
    durations = [cfg.treasury_duration_sensitivity_low, cfg.treasury_primary_modified_duration, cfg.treasury_duration_sensitivity_high]
    for d in durations:
        label = str(d).replace(".", "p")
        tr[f"treasury10y_proxy_return_d{label}"] = prev_y / 12.0 - d * dy

    keep_tr = [c for c in tr.columns if c not in {"Date"}]
    m = sp[["month_end", "source_date_spxt", "spxt_index_level", "spxt_total_return"]].merge(tr[keep_tr], on="month_end", how="outer").sort_values("month_end")
    primary = f"treasury10y_proxy_return_d{str(cfg.treasury_primary_modified_duration).replace('.', 'p')}"
    m["treasury10y_proxy_return"] = m[primary]
    m["benchmark_70_30_return"] = 0.70 * m["spxt_total_return"] + 0.30 * m["treasury10y_proxy_return"]
    m["benchmark_method"] = f"70% SPXT total return + 30% synthetic Treasury duration/carry proxy D={cfg.treasury_primary_modified_duration:.1f}"
    # Market calendar is already monthly; forward 6M directly.
    vals = m["benchmark_70_30_return"].to_numpy(dtype=float)
    fwd = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        w = vals[i + 1:i + 7]
        if len(w) == 6 and np.isfinite(w).all():
            fwd[i] = np.prod(1 + w) - 1
    m["future_6m_benchmark_return"] = fwd
    return m


def build_classification_maps(data_root: Path) -> tuple[pd.DataFrame, set[str]]:
    folder = data_root / "part5_non_individual_holdings"
    epath = folder / "part5_excluded_two_group_enriched.csv"
    rpath = folder / "part5_excluded_individual_stock_like_removed_audit.csv"
    if not epath.exists():
        return pd.DataFrame(), set()
    e = pd.read_csv(epath, usecols=["holding_security_name", "teacher_category", "teacher_subcategory", "classification_confidence"], low_memory=False)
    e["name_key"] = normalize_name(e["holding_security_name"])
    e = e[e["classification_confidence"].astype(str).str.lower().eq("high")].drop_duplicates("name_key")
    removed: set[str] = set()
    if rpath.exists():
        r = pd.read_csv(rpath, usecols=["holding_security_name"], low_memory=False)
        removed = set(normalize_name(r["holding_security_name"]).tolist())
    return e[["name_key", "teacher_category", "teacher_subcategory"]], removed


def load_holdings_events(data_root: Path, cfg: BuildConfig) -> pd.DataFrame:
    folder = data_root / "crsp" / "holdings_raw"
    cols = [
        "crsp_portno", "fund_ticker", "fund_name", "fund_percent_common_stock", "fund_percent_bond", "fund_percent_cash",
        "report_dt", "holding_percent_tna", "holding_security_name", "holding_ticker", "holding_permno",
    ]
    paths = [
        "stock berfore 2010_new___.csv", "stock between 2010_2014_new___.csv",
        "stock between 2015_2019_new___.csv", "stock between 2020_2026_new___.csv",
    ]
    h = pd.concat([pd.read_csv(folder / p, usecols=cols, low_memory=False) for p in paths], ignore_index=True)
    h["report_date_original"] = pd.to_datetime(h["report_dt"], errors="coerce")
    h["month_end"] = month_end(h["report_dt"])
    h = h.dropna(subset=["crsp_portno", "report_date_original", "month_end"]).copy()
    h["crsp_portno"] = pd.to_numeric(h["crsp_portno"], errors="coerce").astype("Int64")

    # Keep last actual report date in a portfolio-month.
    latest = h.groupby(["crsp_portno", "month_end"])["report_date_original"].transform("max")
    h = h[h["report_date_original"].eq(latest)].copy()

    maps, removed = build_classification_maps(data_root)
    h["name_key"] = normalize_name(h["holding_security_name"])
    if not maps.empty:
        h = h.merge(maps, on="name_key", how="left")
    else:
        h["teacher_category"] = np.nan; h["teacher_subcategory"] = np.nan
    h["removed_false_equity"] = h["name_key"].isin(removed)
    h["bucket"] = "unknown"
    h.loc[h["holding_permno"].notna() & ~h["removed_false_equity"], "bucket"] = "equity"
    h.loc[h["teacher_category"].eq("Equity Fund / Stock-fund-like"), "bucket"] = "equity"
    cash_like = h["teacher_subcategory"].fillna("").str.contains(r"cash|money market|liquidity|deposit", case=False, regex=True)
    bond_side = h["teacher_category"].eq("Bond / Credit / Money-related")
    h.loc[bond_side & cash_like, "bucket"] = "cash"
    h.loc[bond_side & ~cash_like, "bucket"] = "bond"
    h["holding_weight"] = pd.to_numeric(h["holding_percent_tna"], errors="coerce") / 100.0

    keys = ["crsp_portno", "month_end", "report_date_original"]
    # Repeated report-level fields: first non-null is sufficient within the selected report date.
    summary = h.groupby(keys, as_index=False).agg(
        fund_ticker_holdings=("fund_ticker", "first"), fund_name_holdings=("fund_name", "first"),
        reported_stock_pct_raw=("fund_percent_common_stock", "first"), reported_bond_pct_raw=("fund_percent_bond", "first"),
        reported_cash_pct_raw=("fund_percent_cash", "first"), holding_record_count=("holding_security_name", "size"),
    )
    pivot = h.pivot_table(index=keys, columns="bucket", values="holding_weight", aggfunc="sum", fill_value=0).reset_index()
    for c in ["equity", "bond", "cash", "unknown"]:
        if c not in pivot: pivot[c] = 0.0
    pivot = pivot.rename(columns={"equity": "holdings_equity_proxy", "bond": "holdings_bond_proxy", "cash": "holdings_cash_proxy", "unknown": "holdings_unknown_proxy"})
    out = summary.merge(pivot, on=keys, how="left")

    for raw, dest in [("reported_stock_pct_raw", "stock_reported"), ("reported_bond_pct_raw", "bond_reported"), ("reported_cash_pct_raw", "cash_reported")]:
        out[dest] = pd.to_numeric(out[raw], errors="coerce") / 100.0
    out["bond_imputed_from_residual"] = out["bond_reported"].isna() & out["stock_reported"].notna() & out["cash_reported"].notna()
    out.loc[out["bond_imputed_from_residual"], "bond_reported"] = 1.0 - out.loc[out["bond_imputed_from_residual"], "stock_reported"] - out.loc[out["bond_imputed_from_residual"], "cash_reported"]
    out["reported_sum"] = out[["stock_reported", "bond_reported", "cash_reported"]].sum(axis=1, min_count=3)
    sane = out[["stock_reported", "bond_reported", "cash_reported"]].ge(-0.10).all(axis=1) & out[["stock_reported", "bond_reported", "cash_reported"]].le(1.20).all(axis=1)
    out["reported_quality"] = "missing"
    out.loc[out["reported_sum"].notna() & sane & out["reported_sum"].between(0.85, 1.15), "reported_quality"] = "high"
    out.loc[out["reported_sum"].notna() & sane & out["reported_sum"].between(0.40, 0.85, inclusive="left"), "reported_quality"] = "partial"
    out.loc[out["reported_sum"].notna() & ~out["reported_quality"].isin(["high", "partial"]), "reported_quality"] = "review"

    out["proxy_known"] = out[["holdings_equity_proxy", "holdings_bond_proxy", "holdings_cash_proxy"]].sum(axis=1)
    proxy_sane = (
        out["proxy_known"].between(cfg.holdings_proxy_min_coverage, cfg.holdings_proxy_max_coverage)
        & out[["holdings_equity_proxy", "holdings_bond_proxy", "holdings_cash_proxy"]].ge(-0.10).all(axis=1)
        & out[["holdings_equity_proxy", "holdings_bond_proxy", "holdings_cash_proxy"]].le(1.20).all(axis=1)
    )
    use_report = out["reported_quality"].isin(["high", "partial"])
    use_proxy = ~use_report & proxy_sane
    out["stock_weight"] = np.where(use_report, out["stock_reported"], np.where(use_proxy, out["holdings_equity_proxy"], np.nan))
    out["bond_weight"] = np.where(use_report, out["bond_reported"], np.where(use_proxy, out["holdings_bond_proxy"], np.nan))
    out["cash_weight"] = np.where(use_report, out["cash_reported"], np.where(use_proxy, out["holdings_cash_proxy"], np.nan))
    out["allocation_source"] = np.select(
        [use_report & out["bond_imputed_from_residual"], use_report, use_proxy],
        ["reported_summary_bond_residual", "reported_summary", "holdings_proxy_high_confidence"], default="unavailable"
    )
    out["allocation_quality"] = np.select([use_report, use_proxy], [out["reported_quality"], "proxy"], default="missing")
    out["allocation_known_coverage"] = np.where(use_report, out["reported_sum"], np.where(use_proxy, out["proxy_known"], np.nan))
    total = out[["stock_weight", "bond_weight", "cash_weight"]].sum(axis=1, min_count=3)
    out["other_unclassified_weight"] = 1.0 - total
    return out.sort_values(["crsp_portno", "month_end"])


def add_changes(events: pd.DataFrame, cfg: BuildConfig) -> pd.DataFrame:
    e = events.sort_values(["crsp_portno", "month_end"]).copy()
    e["prev_month_end"] = e.groupby("crsp_portno")["month_end"].shift(1)
    e["gap_months_from_prev_report"] = e["month_end"].dt.year * 12 + e["month_end"].dt.month - (e["prev_month_end"].dt.year * 12 + e["prev_month_end"].dt.month)
    for c, short in [("stock_weight", "stock"), ("bond_weight", "bond"), ("cash_weight", "cash"), ("other_unclassified_weight", "other_unclassified")]:
        e[f"prev_{c}"] = e.groupby("crsp_portno")[c].shift(1)
        e[f"delta_{short}_pp"] = (e[c] - e[f"prev_{c}"]) * 100.0
    dcols = ["delta_stock_pp", "delta_bond_pp", "delta_cash_pp"]
    e["allocation_change_l1_pp"] = e[dcols].abs().sum(axis=1, min_count=1)
    e["allocation_turnover_pp"] = 0.5 * e["allocation_change_l1_pp"]
    e["has_allocation_change"] = e[dcols].abs().max(axis=1).gt(cfg.change_epsilon_pp)
    e["is_research_change"] = e[dcols].abs().max(axis=1).ge(cfg.research_change_threshold_pp)
    e["comparable_change"] = e["stock_weight"].notna() & e["prev_stock_weight"].notna() & e["gap_months_from_prev_report"].between(1, cfg.max_change_gap_months)
    ds = e["delta_stock_pp"]
    e["allocation_direction"] = np.select([ds >= 1, ds <= -1, ds.abs() < 1], ["risk_on_more_equity", "defensive_less_equity", "roughly_stable_equity"], default="mixed_or_missing")
    return e


def build_all(data_root: Path, cfg: BuildConfig | None = None) -> dict:
    cfg = cfg or BuildConfig(); data_root = Path(data_root); derived = data_root / "derived"; derived.mkdir(parents=True, exist_ok=True)
    fund = load_fund_level(data_root)
    market = load_market(data_root, cfg)
    holdings = load_holdings_events(data_root, cfg)
    e = holdings.merge(fund, on=["crsp_portno", "month_end"], how="left")
    e = e.merge(market, on="month_end", how="left")
    e = add_changes(e, cfg)
    e["future_6m_excess_vs_70_30"] = e["future_6m_fund_gross_return"] - e["future_6m_benchmark_return"]
    e["future_6m_positive_excess"] = np.where(e["future_6m_excess_vs_70_30"].notna(), e["future_6m_excess_vs_70_30"].gt(0).astype(int), np.nan)
    e["event_id"] = "BAL_" + e["crsp_portno"].astype(str) + "_" + e["month_end"].dt.strftime("%Y%m")
    e["event_month"] = e["month_end"].dt.strftime("%Y-%m")
    e["event_eligible_6m"] = e["future_6m_excess_vs_70_30"].notna() & e["comparable_change"]
    e["analysis_entity"] = e["analysis_entity"].fillna("UNRESOLVED MANAGER/FAMILY")
    e["analysis_entity_level"] = e["analysis_entity_level"].fillna("unresolved")
    e["family_name"] = e["family_name"].fillna("Unknown Family")

    e.to_csv(derived / "balanced_allocation_events.csv", index=False)
    fund.to_csv(derived / "balanced_portfolio_month_returns.csv", index=False)
    market.to_csv(derived / "market_70_30_proxy_monthly.csv", index=False)

    audit = {
        "build_config": asdict(cfg),
        "method_warning": "The 30% Treasury leg is a synthetic duration/carry proxy derived from H.15 DGS10 yield, not an official bond total-return series.",
        "event_rows": int(len(e)), "unique_event_ids": int(e["event_id"].nunique()),
        "event_date_min": str(e["month_end"].min().date()), "event_date_max": str(e["month_end"].max().date()),
        "allocation_available": int(e["stock_weight"].notna().sum()),
        "allocation_quality_counts": {str(k): int(v) for k, v in e["allocation_quality"].value_counts(dropna=False).items()},
        "allocation_source_counts": {str(k): int(v) for k, v in e["allocation_source"].value_counts(dropna=False).items()},
        "change_events": int(e["has_allocation_change"].fillna(False).sum()),
        "research_change_events": int(e["is_research_change"].fillna(False).sum()),
        "eligible_future_6m_events": int(e["event_eligible_6m"].fillna(False).sum()),
        "specific_manager_events": int(e["analysis_entity_level"].eq("manager").sum()),
        "fallback_manager_events": int(e["analysis_entity_level"].ne("manager").sum()),
        "fund_return_date_min": str(fund["month_end"].min().date()), "fund_return_date_max": str(fund["month_end"].max().date()),
        "market_date_min": str(market["month_end"].min().date()), "market_date_max": str(market["month_end"].max().date()),
    }
    (derived / "balanced_allocation_events_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (derived / "STEP1_DATA_DICTIONARY.md").write_text("""# Step 1 Data Dictionary\n\n- One event = one actually observed holdings report for a portfolio in a calendar month.\n- `report_date_original` is preserved. `month_end` is the common analysis key. Missing report months are not fabricated.\n- `analysis_entity_level=manager` uses a specific CRSP manager name. Team/Unknown rows explicitly fall back to advisor or fund family.\n- `fund_gross_return_approx = mret + exp_ratio/12`; share classes are aggregated using lagged TNA where possible.\n- If only bond is missing while stock and cash are available, `bond = 1-stock-cash` and a flag is kept.\n- If reported allocation is unavailable, only high-confidence holdings classifications with sufficient coverage are allowed as proxy. Incomplete holdings are not silently normalized to 100%.\n- Allocation changes compare consecutive observed reports, and `gap_months_from_prev_report` is retained.\n- Future 6M = next six complete calendar months t+1...t+6 compounded.\n- S&P leg uses supplied SPXT index levels.\n- H.15 DGS10 is a yield, not total return. The current 30% Treasury leg uses an explicitly labeled duration/carry proxy: previous_yield/12 - D*change_in_yield, primary D=8.5 with sensitivity 7.5 and 9.5.\n""", encoding="utf-8")
    return audit


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--data-root", default="data"); args = p.parse_args()
    print(json.dumps(build_all(Path(args.data_root)), ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
