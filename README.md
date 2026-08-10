# Fin Step 1 — Balanced Fund Allocation Event Quantification

這一版只做研究流程的第一大步：**把 Balanced Fund 的配置行為變成可追蹤的事件資料與 D3 分析介面**。

## 核心事件單位

`Manager/Team-Family × crsp_portno × observed report month`

- 保留 `report_date_original`。
- 再建立 `month_end` 作共同月鍵。
- **沒有 report 的月份不自動製造配置事件**。
- 每個 portfolio 同一月份若有多個 source report date，只保留該月最後一個實際 report date。

## Manager / Team Managed / Unknown

程式不會把 `Team Managed` 或 `Unknown Manager` 假裝成個人：

1. 有明確 `mgr_name` → manager-level entity。
2. `Team Managed` / Unknown / missing → `adv_name` 作 `advisor_team_fallback`。
3. Advisor 也沒有 → `mgmt_name` 作 `family_fallback`。

Dashboard 的 hierarchy 顯示 `Family → Portfolio → Manager/Team fallback`。

## 股 / 債 / 現金配置

優先使用 holdings 檔中的：

- `fund_percent_common_stock`
- `fund_percent_bond`
- `fund_percent_cash`

若**只有 bond 缺值**而 stock、cash 有值：

`bond = 1 - stock - cash`

程式保留 `bond_imputed_from_residual` flag。

若 reported allocation 不可用，程式才會嘗試使用 `part5_excluded_two_group_enriched.csv` 的 **high-confidence** 分類建立 holdings proxy。Proxy coverage 太低、太高或組成不合理時，allocation 保持 missing，不會硬補。

`other_unclassified_weight = 1 - stock - bond - cash` 也保留，不會偷偷把不完整配置 normalize 成 100%。

## 配置變動

同一 portfolio 的連續**實際 report event**相比：

- `delta_stock_pp`
- `delta_bond_pp`
- `delta_cash_pp`
- `allocation_change_l1_pp`
- `allocation_turnover_pp = 0.5 × L1`

`gap_months_from_prev_report` 也保留。研究版預設只把 1–6 個月內的相鄰 report 視為 comparable change。

## 基金報酬

CRSP `mret` 是 net monthly return；`exp_ratio` 為年度 expense ratio。

本研究的近似 gross monthly return：

`gross_return_approx = mret + exp_ratio / 12`

Share classes 先以 lagged TNA 為主要權重聚合到 `crsp_portno × month`。若上一筆 share-class TNA 不是前一個完整月份，才 fallback 到 current TNA。

## Future 6M

對 month t 的 event：

`Future 6M = t+1, t+2, ..., t+6`

用複利：

`(1+r1)(1+r2)...(1+r6)-1`

六個月不完整就保持 missing。

## S&P 500

`spxt_index_1997_2025.csv` 讀取 `PX_LAST`，由 index levels 自己算月報酬：

`SPXT_return_t = SPXT_t / SPXT_(t-1) - 1`

S&P Dow Jones Indices 官方 S&P 500 頁面將 `SPXT` 列為 Total Return Bloomberg ticker：
https://www.spglobal.com/spdji/en/indices/equity/sp-500/

## 10Y Treasury 與 70/30：非常重要

`treasury_10y_1997_2025.csv` 的 `DGS10_Yield` 是 **10-year constant-maturity yield，不是 bond total return**。
Federal Reserve H.15 官方說明：
https://www.federalreserve.gov/releases/h15/

因此現在不能直接寫：

`0.7 × S&P return + 0.3 × DGS10 yield`

這在金融定義上不對。

為了讓 Step 1 可以先跑，程式建立一個**明確標示為 proxy** 的 10Y Treasury return：

`Treasury proxy return ≈ previous_yield/12 - ModifiedDuration × change_in_yield`

Primary Modified Duration = 8.5，並額外輸出 7.5 / 9.5 sensitivity。

Primary 70/30 proxy：

`0.70 × SPXT total return + 0.30 × Treasury duration/carry proxy`

### 論文建議
正式 paper 最好把 30% leg 換成官方 Treasury / Aggregate Bond **Total Return Index**；現在的 Treasury proxy 可保留做 robustness / sensitivity analysis，而不是偽裝成正式 bond total return。

## 執行

```bash
pip install -r requirements.txt
python main.py build
python main.py serve --host 127.0.0.1 --port 5000
```

瀏覽器：`http://127.0.0.1:5000`

也可以一次：

```bash
python main.py all
```

## 產生檔案

`data/derived/`

- `balanced_allocation_events.csv`
- `balanced_portfolio_month_returns.csv`
- `market_70_30_proxy_monthly.csv`
- `balanced_allocation_events_audit.json`
- `STEP1_DATA_DICTIONARY.md`

## Dashboard

1. Family → Portfolio → Manager/Team fallback hierarchy
2. 股 / 債 / 現金配置時間線
3. ΔStock / ΔBond / ΔCash
4. Fund gross monthly return vs 70/30 proxy
5. Allocation change magnitude vs future 6M excess scatter
6. Event table + event detail

這個版本刻意先做「Quantify」，不先做 XGBoost 或 LLM，避免還沒確認事件資料品質就直接建模。
