from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


DATE_COLS = [
    "month_end",
    "report_date_original",
    "prev_month_end",
    "source_date_spxt",
    "source_date_treasury",
]


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
    return [
        {k: json_value(v) for k, v in row.items()}
        for row in df.to_dict("records")
    ]


def safe_text(items: list[str]) -> str | None:
    return items[0] if items else None


def create_app(data_root: Path | str = "data") -> FastAPI:
    project_root = Path(__file__).resolve().parent

    data_root = Path(data_root)
    if not data_root.is_absolute():
        data_root = (project_root / data_root).resolve()

    static_dir = project_root / "static"
    event_path = data_root / "derived" / "balanced_allocation_events.csv"
    market_path = data_root / "derived" / "market_70_30_proxy_monthly.csv"

    app = FastAPI(
        title="Fin - Balanced Fund Event Quantification API",
        version="1.0.0",
        description=(
            "Step 1 API for Manager / Team-Family × Portfolio × Observed Report Month "
            "allocation-event analysis."
        ),
    )

    # Keep the same frontend URL contract as the Flask version:
    # /static/app.js, /static/style.css, ...
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @lru_cache(maxsize=1)
    def event_df() -> pd.DataFrame:
        if not event_path.exists():
            raise FileNotFoundError(
                f"Derived event file not found: {event_path}. "
                "Run: python main.py build"
            )

        df = pd.read_csv(event_path, low_memory=False)
        for c in DATE_COLS:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    @lru_cache(maxsize=1)
    def market_df() -> pd.DataFrame:
        if not market_path.exists():
            raise FileNotFoundError(
                f"Derived market file not found: {market_path}. "
                "Run: python main.py build"
            )

        df = pd.read_csv(market_path, low_memory=False)
        for c in ["month_end", "source_date_spxt", "source_date_treasury"]:
            if c in df.columns:
                df[c] = pd.to_datetime(df[c], errors="coerce")
        return df

    @app.get("/")
    async def index():
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(index_path)

    @app.get("/api/health")
    async def health():
        try:
            df = event_df()
            return {
                "ok": True,
                "events": int(len(df)),
                "event_file": str(event_path),
                "framework": "FastAPI",
            }
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": str(exc)},
            )

    @app.get("/api/meta")
    async def meta():
        df = event_df()

        return {
            "event_count": int(len(df)),
            "unique_portfolios": int(df["crsp_portno"].nunique()),
            "unique_entities": int(df["analysis_entity"].nunique()),
            "date_min": df["month_end"].min().strftime("%Y-%m-%d"),
            "date_max": df["month_end"].max().strftime("%Y-%m-%d"),
            "allocation_available": int(df["stock_weight"].notna().sum()),
            "change_events": int(
                df["has_allocation_change"]
                .fillna(False)
                .astype(bool)
                .sum()
            ),
            "eligible_6m": int(
                df["event_eligible_6m"]
                .fillna(False)
                .astype(bool)
                .sum()
            ),
            "benchmark_warning": (
                "30% Treasury leg is a synthetic duration/carry proxy from "
                "the H.15 10Y yield, not an official bond total-return series."
            ),
        }

    @app.get("/api/entities")
    async def entities(request: Request):
        df = event_df()

        q = request.query_params.get("q", "").strip().lower()

        e = (
            df.groupby(
                ["analysis_entity", "analysis_entity_level", "family_name"],
                dropna=False,
            )
            .agg(
                event_count=("event_id", "count"),
                portfolio_count=("crsp_portno", "nunique"),
                first_month=("month_end", "min"),
                last_month=("month_end", "max"),
            )
            .reset_index()
        )

        if q:
            mask = (
                e["analysis_entity"]
                .astype(str)
                .str.lower()
                .str.contains(q, regex=False)
                |
                e["family_name"]
                .astype(str)
                .str.lower()
                .str.contains(q, regex=False)
            )
            e = e[mask]

        e = (
            e.sort_values(
                ["event_count", "analysis_entity"],
                ascending=[False, True],
            )
            .head(1000)
        )

        return records(e)

    @app.get("/api/portfolios")
    async def portfolios(request: Request):
        df = event_df()

        entity = request.query_params.get("entity")
        if entity:
            df = df[df["analysis_entity"].eq(entity)]

        p = (
            df.groupby(
                [
                    "crsp_portno",
                    "fund_name",
                    "fund_ticker",
                    "analysis_entity",
                    "family_name",
                ],
                dropna=False,
            )
            .agg(
                event_count=("event_id", "count"),
                first_month=("month_end", "min"),
                last_month=("month_end", "max"),
            )
            .reset_index()
            .sort_values("event_count", ascending=False)
        )

        return records(p.head(2000))

    def apply_filters(df: pd.DataFrame, request: Request) -> pd.DataFrame:
        params = request.query_params

        entity = params.get("entity")
        family = params.get("family")
        portno = params.get("portno")
        start = params.get("start")
        end = params.get("end")
        only_changes = params.get("only_changes", "false").lower() == "true"
        quality = params.get("quality")

        try:
            min_change = float(params.get("min_change", "0") or 0)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="min_change must be numeric",
            ) from exc

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
            df = df[
                df["has_allocation_change"]
                .fillna(False)
                .astype(bool)
            ]

        if min_change > 0:
            df = df[
                pd.to_numeric(
                    df["allocation_change_l1_pp"],
                    errors="coerce",
                ) >= min_change
            ]

        if quality:
            df = df[df["allocation_quality"].eq(quality)]

        return df

    @app.get("/api/events")
    async def events(request: Request):
        df = apply_filters(event_df().copy(), request)
        params = request.query_params

        try:
            limit = min(int(params.get("limit", "5000")), 10000)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="limit must be an integer",
            ) from exc

        sort = params.get("sort", "month_end")
        ascending = params.get("ascending", "true").lower() == "true"

        if sort in df.columns:
            df = df.sort_values(sort, ascending=ascending)

        return records(df.head(limit))

    @app.get("/api/timeline")
    async def timeline(request: Request):
        df = apply_filters(event_df().copy(), request)

        cols = [
            "event_id",
            "crsp_portno",
            "month_end",
            "report_date_original",
            "analysis_entity",
            "analysis_entity_level",
            "family_name",
            "fund_name",
            "stock_weight",
            "bond_weight",
            "cash_weight",
            "other_unclassified_weight",
            "allocation_source",
            "allocation_quality",
            "delta_stock_pp",
            "delta_bond_pp",
            "delta_cash_pp",
            "allocation_change_l1_pp",
            "allocation_direction",
            "fund_net_return",
            "fund_gross_return_approx",
            "spxt_total_return",
            "treasury10y_yield_pct",
            "treasury10y_proxy_return",
            "benchmark_70_30_return",
            "future_6m_fund_gross_return",
            "future_6m_benchmark_return",
            "future_6m_excess_vs_70_30",
            "event_eligible_6m",
            "gap_months_from_prev_report",
        ]

        cols = [c for c in cols if c in df.columns]

        return records(
            df[cols]
            .sort_values("month_end")
            .head(10000)
        )

    @app.get("/api/event/{event_id}")
    async def event_detail(event_id: str):
        df = event_df()
        g = df[df["event_id"].eq(event_id)]

        if g.empty:
            raise HTTPException(status_code=404, detail="event not found")

        return records(g)[0]

    @app.get("/api/hierarchy")
    async def hierarchy():
        df = event_df()

        # Family -> portfolio -> manager/fallback entity.
        # Team Managed / Unknown remains a fallback entity,
        # never pretending to be an individual manager.
        d = df[
            [
                "family_name",
                "crsp_portno",
                "fund_name",
                "analysis_entity",
                "analysis_entity_level",
            ]
        ].drop_duplicates()

        families = []

        for family, fg in d.groupby("family_name", dropna=False):
            portfolios = []

            for portno, pg in fg.groupby("crsp_portno"):
                entities = [
                    {
                        "name": r["analysis_entity"],
                        "level": r["analysis_entity_level"],
                    }
                    for _, r in (
                        pg[
                            [
                                "analysis_entity",
                                "analysis_entity_level",
                            ]
                        ]
                        .drop_duplicates()
                        .iterrows()
                    )
                ]

                portfolios.append(
                    {
                        "crsp_portno": int(portno),
                        "fund_name": safe_text(
                            pg["fund_name"]
                            .dropna()
                            .astype(str)
                            .head(1)
                            .tolist()
                        ),
                        "entities": entities,
                    }
                )

            families.append(
                {
                    "family": str(family),
                    "portfolios": portfolios,
                }
            )

        families.sort(
            key=lambda x: len(x["portfolios"]),
            reverse=True,
        )

        return families[:100]

    @app.get("/api/market")
    async def market(request: Request):
        df = market_df().copy()
        params = request.query_params

        start = params.get("start")
        end = params.get("end")

        if start:
            df = df[df["month_end"] >= pd.Timestamp(start)]

        if end:
            df = df[df["month_end"] <= pd.Timestamp(end)]

        return records(df)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="127.0.0.1",
        port=5000,
        reload=True,
    )
