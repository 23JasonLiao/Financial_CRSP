from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-root", default="data")
    args = p.parse_args()
    root = Path(args.data_root)
    event_path = root / "derived" / "balanced_allocation_events.csv"
    market_path = root / "derived" / "market_70_30_proxy_monthly.csv"
    e = pd.read_csv(event_path, low_memory=False)
    m = pd.read_csv(market_path, low_memory=False)
    e["month_end"] = pd.to_datetime(e["month_end"], errors="coerce")
    e["report_date_original"] = pd.to_datetime(e["report_date_original"], errors="coerce")
    m["month_end"] = pd.to_datetime(m["month_end"], errors="coerce")

    checks: dict[str, object] = {}
    checks["event_id_unique"] = bool(e["event_id"].is_unique)
    checks["month_end_is_calendar_month_end"] = bool((e["month_end"] == e["month_end"].dt.to_period("M").dt.to_timestamp("M")).all())
    checks["original_report_not_after_month_end"] = bool((e["report_date_original"] <= e["month_end"]).all())
    generic = e["manager_raw"].fillna("").astype(str).str.strip().str.lower().isin(["team managed", "team manager", "unknown manager", "unknown", ""])
    checks["generic_manager_never_labeled_manager_level"] = bool(~(generic & e["analysis_entity_level"].eq("manager")).any())

    implied = e["bond_imputed_from_residual"].fillna(False).astype(bool)
    if implied.any():
        # Only compare rows where final allocation came from the reported summary.
        g = e[implied & e["allocation_source"].astype(str).str.startswith("reported_summary")]
        err = (g["stock_weight"] + g["bond_weight"] + g["cash_weight"] - 1).abs()
        checks["bond_residual_max_abs_error"] = float(err.max()) if not g.empty else None
        checks["bond_residual_pass"] = bool(g.empty or err.max() < 1e-9)
    else:
        checks["bond_residual_pass"] = True

    # Validate the forward 6M benchmark on the monthly market table.
    r = pd.to_numeric(m["benchmark_70_30_return"], errors="coerce").to_numpy(dtype=float)
    target = pd.to_numeric(m["future_6m_benchmark_return"], errors="coerce").to_numpy(dtype=float)
    errors = []
    for i in range(len(m)):
        w = r[i + 1:i + 7]
        if len(w) == 6 and np.isfinite(w).all() and np.isfinite(target[i]):
            calc = np.prod(1 + w) - 1
            errors.append(abs(calc - target[i]))
    checks["future_6m_benchmark_max_abs_error"] = float(max(errors)) if errors else None
    checks["future_6m_benchmark_pass"] = bool(not errors or max(errors) < 1e-12)

    checks["allocation_source_counts"] = e["allocation_source"].value_counts(dropna=False).to_dict()
    checks["allocation_quality_counts"] = e["allocation_quality"].value_counts(dropna=False).to_dict()
    checks["event_rows"] = int(len(e))
    checks["date_min"] = str(e["month_end"].min().date())
    checks["date_max"] = str(e["month_end"].max().date())
    checks["PASS"] = bool(all(v for k, v in checks.items() if k.endswith("_pass") or k in {"event_id_unique", "month_end_is_calendar_month_end", "original_report_not_after_month_end", "generic_manager_never_labeled_manager_level"}))

    out_path = root / "derived" / "STEP1_VALIDATION.json"
    out_path.write_text(json.dumps(checks, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
