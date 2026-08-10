# Step 1 Data Dictionary

- One event = one actually observed holdings report for a portfolio in a calendar month.
- `report_date_original` is preserved. `month_end` is the common analysis key. Missing report months are not fabricated.
- `analysis_entity_level=manager` uses a specific CRSP manager name. Team/Unknown rows explicitly fall back to advisor or fund family.
- `fund_gross_return_approx = mret + exp_ratio/12`; share classes are aggregated using lagged TNA where possible.
- If only bond is missing while stock and cash are available, `bond = 1-stock-cash` and a flag is kept.
- If reported allocation is unavailable, only high-confidence holdings classifications with sufficient coverage are allowed as proxy. Incomplete holdings are not silently normalized to 100%.
- Allocation changes compare consecutive observed reports, and `gap_months_from_prev_report` is retained.
- Future 6M = next six complete calendar months t+1...t+6 compounded.
- S&P leg uses supplied SPXT index levels.
- H.15 DGS10 is a yield, not total return. The current 30% Treasury leg uses an explicitly labeled duration/carry proxy: previous_yield/12 - D*change_in_yield, primary D=8.5 with sensitivity 7.5 and 9.5.
