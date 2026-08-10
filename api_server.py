from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify, request, send_from_directory


DATE_COLS = ["month_end", "report_date_original", "prev_month_end", "source_date_spxt", "source_date_treasury"]


def json_value(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if not np.isfinite(v) else float(v)
    if pd.isna(v):
        return None
    return v


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: json_value(v) for k, v in row.items()} for row in df.to_dict("records")]


def create_app(data_root: Path | str = "data") -> Flask:
    project_root = Path(__file__).resolve().parent
    data_root = Path(data_root)
    if not data_root.is_absolute():
        data_root = (project_root / data_root).resolve()
    static_dir = project_root / "static"
    event_path = data_root / "derived" / "balanced_allocation_events.csv"
    market_path = data_root / "derived" / "market_70_30_proxy_monthly.csv"

    app = Flask(__name__, static_folder=None)

    @lru_cache(maxsize=1)
    def event_df() -> pd.DataFrame:
        if not event_path.exists():
            raise FileNotFoundError(f"Derived event file not found: {event_path}. Run: python main.py build")
        df = pd.read_csv(event_path, low_memory=False)
        for c in DATE_COLS:
            if c in df:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    @lru_cache(maxsize=1)
    def market_df() -> pd.DataFrame:
        df = pd.read_csv(market_path, low_memory=False)
        for c in ["month_end", "source_date_spxt", "source_date_treasury"]:
            if c in df:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(static_dir, name)

    @app.get("/api/health")
    def health():
        try:
            df = event_df()
            return jsonify({"ok": True, "events": int(len(df)), "event_file": str(event_path)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/meta")
    def meta():
        df = event_df()
        out = {
            "event_count": int(len(df)),
            "unique_portfolios": int(df["crsp_portno"].nunique()),
            "unique_entities": int(df["analysis_entity"].nunique()),
            "date_min": df["month_end"].min().strftime("%Y-%m-%d"),
            "date_max": df["month_end"].max().strftime("%Y-%m-%d"),
            "allocation_available": int(df["stock_weight"].notna().sum()),
            "change_events": int(df["has_allocation_change"].fillna(False).astype(bool).sum()),
            "eligible_6m": int(df["event_eligible_6m"].fillna(False).astype(bool).sum()),
            "benchmark_warning": "30% Treasury leg is a synthetic duration/carry proxy from the H.15 10Y yield, not an official bond total-return series.",
        }
        return jsonify(out)

    @app.get("/api/entities")
    def entities():
        df = event_df()
        q = request.args.get("q", "").strip().lower()
        e = (
            df.groupby(["analysis_entity", "analysis_entity_level", "family_name"], dropna=False)
            .agg(event_count=("event_id", "count"), portfolio_count=("crsp_portno", "nunique"), first_month=("month_end", "min"), last_month=("month_end", "max"))
            .reset_index()
        )
        if q:
            mask = e["analysis_entity"].astype(str).str.lower().str.contains(q, regex=False) | e["family_name"].astype(str).str.lower().str.contains(q, regex=False)
            e = e[mask]
        e = e.sort_values(["event_count", "analysis_entity"], ascending=[False, True]).head(1000)
        return jsonify(records(e))

    @app.get("/api/portfolios")
    def portfolios():
        df = event_df()
        entity = request.args.get("entity")
        if entity:
            df = df[df["analysis_entity"].eq(entity)]
        p = (
            df.groupby(["crsp_portno", "fund_name", "fund_ticker", "analysis_entity", "family_name"], dropna=False)
            .agg(event_count=("event_id", "count"), first_month=("month_end", "min"), last_month=("month_end", "max"))
            .reset_index()
            .sort_values("event_count", ascending=False)
        )
        return jsonify(records(p.head(2000)))

    def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
        entity = request.args.get("entity")
        family = request.args.get("family")
        portno = request.args.get("portno")
        start = request.args.get("start")
        end = request.args.get("end")
        only_changes = request.args.get("only_changes", "false").lower() == "true"
        min_change = float(request.args.get("min_change", "0") or 0)
        quality = request.args.get("quality")
        if entity:
            df = df[df["analysis_entity"].eq(entity)]
        if family:
            df = df[df["family_name"].eq(family)]
        if portno:
            df = df[df["crsp_portno"].astype(str).eq(str(portno))]
        if start:
            df = df[df["month_end"] >= pd.Timestamp(start)]
        if end:
            df = df[df["month_end"] <= pd.Timestamp(end)]
        if only_changes:
            df = df[df["has_allocation_change"].fillna(False).astype(bool)]
        if min_change > 0:
            df = df[pd.to_numeric(df["allocation_change_l1_pp"], errors="coerce") >= min_change]
        if quality:
            df = df[df["allocation_quality"].eq(quality)]
        return df

    @app.get("/api/events")
    def events():
        df = apply_filters(event_df().copy())
        limit = min(int(request.args.get("limit", "5000")), 10000)
        sort = request.args.get("sort", "month_end")
        ascending = request.args.get("ascending", "true").lower() == "true"
        if sort in df.columns:
            df = df.sort_values(sort, ascending=ascending)
        return jsonify(records(df.head(limit)))

    @app.get("/api/timeline")
    def timeline():
        df = apply_filters(event_df().copy())
        cols = [
            "event_id", "crsp_portno", "month_end", "report_date_original", "analysis_entity", "analysis_entity_level",
            "family_name", "fund_name", "stock_weight", "bond_weight", "cash_weight", "other_unclassified_weight",
            "allocation_source", "allocation_quality", "delta_stock_pp", "delta_bond_pp", "delta_cash_pp",
            "allocation_change_l1_pp", "allocation_direction", "fund_net_return", "fund_gross_return_approx",
            "spxt_total_return", "treasury10y_yield_pct", "treasury10y_proxy_return", "benchmark_70_30_return",
            "future_6m_fund_gross_return", "future_6m_benchmark_return", "future_6m_excess_vs_70_30",
            "event_eligible_6m", "gap_months_from_prev_report",
        ]
        cols = [c for c in cols if c in df.columns]
        return jsonify(records(df[cols].sort_values("month_end").head(10000)))

    @app.get("/api/event/<event_id>")
    def event_detail(event_id: str):
        df = event_df()
        g = df[df["event_id"].eq(event_id)]
        if g.empty:
            return jsonify({"error": "event not found"}), 404
        return jsonify(records(g)[0])

    @app.get("/api/hierarchy")
    def hierarchy():
        df = event_df()
        # Family -> portfolio -> manager/fallback entity. This mirrors the expert view
        # without pretending a Team Managed row is an individual manager.
        d = df[["family_name", "crsp_portno", "fund_name", "analysis_entity", "analysis_entity_level"]].drop_duplicates()
        families = []
        for family, fg in d.groupby("family_name", dropna=False):
            portfolios = []
            for portno, pg in fg.groupby("crsp_portno"):
                entities = [
                    {"name": r["analysis_entity"], "level": r["analysis_entity_level"]}
                    for _, r in pg[["analysis_entity", "analysis_entity_level"]].drop_duplicates().iterrows()
                ]
                portfolios.append({
                    "crsp_portno": int(portno),
                    "fund_name": safe_text(pg["fund_name"].dropna().astype(str).head(1).tolist()),
                    "entities": entities,
                })
            families.append({"family": str(family), "portfolios": portfolios})
        families.sort(key=lambda x: len(x["portfolios"]), reverse=True)
        return jsonify(families[:100])

    @app.get("/api/market")
    def market():
        df = market_df().copy()
        start = request.args.get("start")
        end = request.args.get("end")
        if start:
            df = df[df["month_end"] >= pd.Timestamp(start)]
        if end:
            df = df[df["month_end"] <= pd.Timestamp(end)]
        return jsonify(records(df))

    return app


def safe_text(items: list[str]) -> str | None:
    return items[0] if items else None


if __name__ == "__main__":
    create_app().run(debug=True)
