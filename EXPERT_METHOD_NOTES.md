# Expert review notes — Step 1 event quantification

## 1. Unit of analysis
One event is an actually observed holdings report for `crsp_portno` in a calendar month. `report_date_original` is retained and a separate `month_end` key is created for monthly alignment. Missing reports are not fabricated.

## 2. Manager identity
Specific `mgr_name` values remain manager-level. Generic values such as Team Managed / Unknown / missing are not treated as individuals; they explicitly fall back to `adv_name`, then `mgmt_name`. This prevents false manager identities while preserving a usable organizational analysis entity.

## 3. Allocation
Reported stock/bond/cash summary is primary. Only when bond alone is missing and stock/cash are present is bond computed as `1-stock-cash`, with an explicit flag. If the reported summary is unavailable, only high-confidence holdings classifications with sufficient coverage are allowed as a proxy; incomplete holdings are not normalized to 100%.

## 4. Allocation change
Changes compare consecutive observed reports for the same portfolio. `gap_months_from_prev_report` remains visible. The data builder does not assume that every portfolio truly has a holdings report every month simply because the analysis uses a monthly key.

## 5. Fund return
At share-class level: `gross_return_approx = mret + exp_ratio/12`. Share classes are aggregated to portfolio-month using lagged TNA when an adjacent prior month exists, otherwise current TNA. The word `approx` is retained because expense ratios are annualized and this is a standard additive approximation rather than a reconstructed pre-fee NAV return.

## 6. Future six-month target
For an event in month t, outcome uses the next six complete calendar months t+1...t+6 and compounds them. Missing one of the six months makes the target unavailable.

## 7. Equity benchmark
The supplied `spxt_index_1997_2025.csv` is treated as S&P 500 Total Return index levels and monthly return is reconstructed from adjacent index levels.
Official S&P 500 page: https://www.spglobal.com/spdji/en/indices/equity/sp-500/

## 8. Treasury warning
The supplied `treasury_10y_1997_2025.csv` contains a 10-year constant-maturity **yield**, not a bond total-return index. It must not be directly added as a 30% monthly return.
Federal Reserve H.15: https://www.federalreserve.gov/releases/h15/

For Step 1 only, the code creates a transparent synthetic return proxy:

`r_bond_proxy ~= previous_yield/12 - ModifiedDuration * change_in_yield`

Primary D=8.5, sensitivity D=7.5 and 9.5. The 70/30 result is explicitly labeled a proxy. For the final paper, an official Treasury/Aggregate Bond Total Return Index should replace the 30% leg if available; the yield-based proxy can remain as robustness analysis.

## 9. Why this is conservative
The design prefers missing values and quality flags over silently inventing managers, reports, allocations, or bond returns. This is important because the later XGBoost/LLM stages should learn from traceable events, not preprocessing artifacts.
