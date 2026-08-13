# Claude Code Step 1 審查報告與 Changelog

> 依照 `CLAUDE_CODE_STEP1_REVIEW_AND_CHANGELOG_BRIEF.md`（`CLAUDE_CODE_STEP1_REVIEW_BRIEF.md`）指示完成的審查與最小化修改紀錄。
> 角色：research-code auditor + minimal bug fixer。未新增 Step 2 / XGBoost / SHAP / LLM / RAG / Equity Fund，未修改 research 方法論或 threshold。

---

## 1. Executive Summary

**OVERALL STATUS：PASS WITH WARNINGS**

- Step 1 的核心研究骨架（event identity、month-end 定義、report date 保存、manager fallback 邏輯、bond residual 規則、70/30 proxy 公式、Future 6M calendar compounding、temporal leakage 紀律）在程式碼層級是**正確且自洽**的，且透過本次擴充的 validator 得到了比原本嚴謹得多的驗證覆蓋。
- 本次審查**修正了 4 個明確的程式錯誤**（詳見第 5 節），全部屬於「off-by-one / timezone / API 錯誤處理 / validator 遺漏必要 deterministic test」這類與研究方法論無關的 bug，未改動任何研究定義、threshold 或 event 語意。
- 本次審查也**新發現 1 個先前完全沒有被驗證到的資料一致性問題**（`fund_gross_return_approx` 與 `expense_ratio_annual` 在多 share-class 且 expense_ratio 部分缺失的 portfolio-month 中互不吻合，見 4.5 節與 Finding #5）。這個問題**沒有**被自動修改，因為修法有兩種以上合理選擇，牽涉「missing expense ratio 該如何處理」這個明確被 brief 列為研究者決定事項的主題。
- 除此之外，還有數個**已知但刻意保留**的設計取捨（partial allocation 涵蓋率門檻、`comparable_change` 未檢查 bond/cash/quality、L1 在 component 缺值時的行為、Treasury proxy 為暫時性），這些在原始碼中都可追溯、有清楚標記，且都已在本報告中列出，供研究者在進入 Step 2 前明確裁決。
- **是否可以繼續 Step 2**：可以，但建議先就第 11 節「Remaining Risks Before Step 2」中的 5 個項目做出明確決定（尤其是 4.5 節的 gross return / expense_ratio 不一致問題與 comparable_change 的 quality-gating 問題），因為這兩者會直接影響任何以 allocation change 或 fund return 做為 feature 的下游分析。
- **是否有 blocking issue**：沒有會讓 Step 1 資料無法使用的阻斷性問題。所有已修正的 bug 都屬於「介面 / 驗證工具」層級，未曾污染 `data/derived/*.csv` 內的研究數據本身。

---

## 2. Files Reviewed

依 brief 第 5 節僅審查並可修改以下 7 個實際檔案（其專案內實際路徑）：

| 檔案 | 實際路徑 |
|---|---|
| `build_balanced_events.py` | `scripts/build_balanced_events.py` |
| `validate_step1.py` | `scripts/validate_step1.py` |
| `api_server.py` | `api_server.py` |
| `main.py` | `main.py` |
| `app.js` | `static/app.js` |
| `index.html` | `static/index.html` |
| `style.css` | `static/style.css` |

另外閱讀（唯讀，未修改）以下作為審查背景：

- `data/crsp/fund_level/balanced_before2010.csv`、`balanced_after2010.csv`（header 抽樣）
- `data/crsp/holdings_raw/*.csv`（header 抽樣，4 個檔案）
- `data/market/spxt_index_1997_2025.csv`、`data/market/treasury_10y_1997_2025.csv`
- `data/part5_non_individual_holdings/*.csv`（分類與 false-equity audit 來源）
- `data/derived/balanced_allocation_events.csv`、`balanced_portfolio_month_returns.csv`、`market_70_30_proxy_monthly.csv`
- `data/derived/balanced_allocation_events_audit.json`、`data/derived/STEP1_VALIDATION.json`（修改前後皆讀取，用於前後比對）
- 現有的 `.venv`（已安裝 fastapi/uvicorn/pandas/numpy）用於實際執行 build / validate / 啟動 API 做 smoke test

---

## 3. Research Semantics Verification

| 項目 | 結果 | 說明 |
|---|---|---|
| Event 定義（crsp_portno × calendar month，取月內最後一次實際 report_dt） | **PASS** | `load_holdings_events()` 以 `report_date_original` 的 `transform("max")` 篩選，且 `event_id` 唯一性經 validator 確認（32,907 rows，`event_id_unique=true`, `portfolio_month_unique=true`）。 |
| month_end 僅為分析 key，`report_date_original` 完整保留 | **PASS** | CSV 中兩欄同時存在；validator 確認 `original_report_not_after_month_end=true`。 |
| 不製造不存在的 event（無 report 的月份不補) | **PASS** | Holdings 與 fund 皆以「實際觀察到的列」為準，沒有任何 forward-fill 或月份填補邏輯；Future 6M 的計算是對「目標視窗」reindex，不是對「event 本身」reindex，兩者不會混淆（見第 8 節）。 |
| Manager / Team / Family fallback（manager > advisor/team > family > unresolved，generic 不可冒充 manager） | **PASS** | Validator 確認 `generic_manager_never_labeled_manager_level=true`（且本次已改為直接重用 `build_balanced_events.py` 的 `GENERIC_MANAGER_NAMES`，見 Finding #4）。 |
| Allocation 定義（bond residual 僅在 stock+cash 皆可用時才推算，不可硬 normalize 到 100%） | **WARNING** | Residual 公式本身精確無誤（`bond_residual_pass=true`）；但 `"partial"` 品質層涵蓋率下限僅 40%，且佔可用 allocation 的 66%，遠多於 `"high"`（7%）；`comparable_change` 與 `event_eligible_6m` 都未對 quality 做任何要求。詳見 Finding #6、#9（Researcher Decision Required）。 |
| Return 定義（net vs gross approx = mret + exp_ratio/12，需保留兩者） | **WARNING** | 兩欄位皆保留無誤；但發現多 share-class 且 `exp_ratio` 部分缺失時，`fund_gross_return_approx - fund_net_return` 與 `expense_ratio_annual/12` 不吻合（Finding #5，Researcher Decision Required）。 |
| 70/30 proxy（SPXT total return + Treasury yield-based duration/carry proxy，明確標示為 proxy 非官方 bond total return） | **PASS** | 公式重新計算完全吻合（見第 9 節）；且在 `build_config.method_warning`、`STEP1_DATA_DICTIONARY.md`、`/api/meta` 的 `benchmark_warning`、前端 `method-pill` 與 event detail 警語中都有一致揭露。 |
| Future 6M（t+1…t+6 完整 calendar month 複利，缺一即 NaN） | **PASS** | 本次新增了 fund 層級的獨立重算驗證（74,653 個視窗，最大誤差 1.7e-15），先前 validator 完全沒有測這一項，只測了 benchmark 層級。 |
| Excess return（fund gross − benchmark，同一組 calendar month） | **PASS** | `future_6m_excess_exact=true`，兩邊視窗完全一致。 |
| All Observed Events vs Special Events（dataset 本身是全部觀察到的 event，UI 預設只顯示 changes 但可切換） | **PASS** | `balanced_allocation_events.csv` 含全部 32,907 個觀察到的 portfolio-month，`onlyChanges` 只是前端預設勾選的 checkbox（可取消），不影響底層資料完整性。 |

---

## 4. Findings Table

Severity：CRITICAL / HIGH / MEDIUM / LOW / INFO　｜　Action：FIXED（已直接修正）/ DECISION-REQUIRED（研究者決定）/ DOCUMENTED（僅記錄）

| # | Severity | File | Area | Finding | Impact | Action |
|---|---|---|---|---|---|---|
| 1 | CRITICAL | `api_server.py` | FastAPI | `apply_filters()` 與 `/api/market` 對 `start`/`end` query 參數直接用 `pd.Timestamp(value)`，無效日期字串（如 `notadate`、`2020-99-99`）會丟出未捕捉的 `ValueError`，FastAPI 回傳裸的 HTTP 500。 | 使用者輸入格式錯誤的日期即造成伺服器錯誤，而非乾淨的 4xx；已用 curl 實測重現（HTTP 500）。 | **FIXED** |
| 2 | HIGH | `api_server.py` | FastAPI | 除 `/api/health` 外，其餘 6 個 endpoint 在 derived CSV 不存在時（尚未 `python main.py build`）都會讓 `FileNotFoundError` 直接穿透，回傳裸的 HTTP 500。 | 對「尚未 build」這個常見情境，錯誤訊息不明確、狀態碼不恰當；已用 `TestClient`-替代方式（直接跑 uvicorn 指向不存在路徑）重現。 | **FIXED** |
| 3 | CRITICAL | `static/app.js` | Frontend/D3 | `apiParams()` 中 `endDate` 篩選用 `new Date(y,m,0).toISOString().slice(0,10)`：`new Date(y,m,0)` 建立的是**本地時區**午夜，`toISOString()` 轉為 UTC 字串。對時區早於 UTC（如台灣 UTC+8，正是本專案 UI 的目標使用者）的使用者，本地月底午夜換算成 UTC 會落回前一天，造成 `end` 篩選少算一天，month-end 事件被漏篩。 | 使用者在台灣時區設定「結束月份 = 2020-03」，實際送給 API 的 `end` 會是 `2020-03-30` 而非 `2020-03-31`，導致 3/31 的 event 被排除。屬 brief 18.1.A 明確點名的高風險項目，已確認為真實 bug。 | **FIXED** |
| 4 | CRITICAL | `scripts/validate_step1.py` | Validation | 原始 validator 僅實作約 6 項檢查，遠少於 brief 第 19 節要求的完整 checklist（identity/calendar/manager/allocation/change/return/benchmark/future 6M/merge coverage 共約 40 項）。且其 generic manager 檢查用了一份**手寫、較短、與 builder 的 `GENERIC_MANAGER_NAMES` 不一致**的清單（缺少 "multiple managers"、"management team"、"not disclosed"、"n/a"、"na"、"none" 等），若未來 builder 對這些值誤判為 manager，validator 不會抓到。 | Validator 名不符實：`PASS: true` 只代表 6 項檢查通過，不代表資料真的通過完整審查；且 generic manager 檢查本身有邏輯漏洞。 | **FIXED**（見第 6 節詳述） |
| 5 | HIGH | `scripts/build_balanced_events.py`（**未修改，僅記錄**） | Return Logic / Share-class Aggregation | 透過新 validator 發現：`fund_gross_return_approx - fund_net_return` 應等於 `expense_ratio_annual/12`，但在多 share-class 且部分 share-class 缺 `exp_ratio` 的 portfolio-month 中不成立。根因：`fund_gross_return_approx` 是先在 share-class 層級算好 `mret + exp_ratio.fillna(0)/12` 再用 TNA 加權平均（有效列＝`mret` 非空即可），而 `expense_ratio_annual` 是**另外獨立**對 `exp_ratio` 做 TNA 加權平均（有效列＝`exp_ratio` 非空，缺值的 share class 整列被排除、分母也跟著變），兩者用了不同的有效列集合與分母。當同一 portfolio-month 內不同 share class 的 `exp_ratio` 缺失情況不同時，兩個獨立加權平均就會不吻合。實測：受影響 1,250 / 20,109 個可檢查列（≈6.2%），全部發生在 `share_class_count > 1` 的情況，最大誤差 0.00067（月度，約當年化 0.8pp）。 | 這代表 `expense_ratio_annual` 這個「揭露用診斷欄位」目前**不能**用來反推 `fund_gross_return_approx` 實際內含的費用假設，兩者在多 share-class 基金上會兜不起來。`fund_gross_return_approx` 本身的計算沒有錯（仍是正確的加權平均），問題出在對外揭露的 `expense_ratio_annual` 欄位口徑不一致。 | **DECISION-REQUIRED**（未修改；有兩種以上合理修法，且牽涉「missing expense ratio 視為 0%」這個 brief 明確保留給研究者的政策問題，見 11.1 節） |
| 6 | HIGH | `scripts/build_balanced_events.py`（未修改） | Allocation Logic / Research Semantics | `comparable_change`（進而 `event_eligible_6m`）只檢查 `stock_weight`、`prev_stock_weight` 非空且 `gap_months_from_prev_report` 在 1–6 個月內，**完全沒有**檢查 bond/cash 是否同時可比，也沒有檢查 current/previous 兩期的 `allocation_quality`／`allocation_source` 是否一致。新 validator 量化結果：`comparable_change` 為真的事件中，3,527 筆的 `allocation_source` 與前一期不同、3,625 筆的 `allocation_quality` 與前一期不同、10,842 筆「current 或 previous 至少一邊不是 high quality」。 | 目前被視為「可比較的配置變化」事件，有相當比例其實混雜了資料來源／品質切換（例如從 proxy 換成 reported，或從 partial 換成 high），這可能被誤解為 manager 的真實配置行為。這正是 brief 10.2 節 #3、#5 與 20 節 #2、#3 明確點名、要求「不要自行假設一定要改」的項目。 | **DECISION-REQUIRED** |
| 7 | HIGH | `scripts/build_balanced_events.py`（未修改） | Research Semantics | `allocation_quality="partial"` 的涵蓋率下限僅 0.40（即最多 60% 配置未知），佔可用配置的 66%（11,633 / 17,679），遠高於 `"high"` 的 7%（1,305）。由於 Finding #6，這些 partial 品質的觀測值目前與 high-quality 觀測值被同等對待地餵入 change/eligibility 分析。 | 若不處理，任何以「配置變化幅度 vs 未來超額報酬」為主軸的分析，樣本主力將是最多只有 40% 已知配置的觀測值。 | **DECISION-REQUIRED**（`research_change_threshold_pp`、`holdings_proxy_min_coverage` 等皆為 `BuildConfig` 中明列、brief 禁止自行更動的參數） |
| 8 | MEDIUM | `scripts/build_balanced_events.py`（未修改） | Allocation Logic | `allocation_change_l1_pp = e[dcols].abs().sum(axis=1, min_count=1)`：當 stock/bond/cash 三個 delta 只有部分非空時，缺的分量會被當成 0 貢獻，而非讓 L1 變成 NaN，可能產生「看似完整」但實際不完整的 L1。目前資料中此情形發生次數為 **0**（`allocation_change_l1_pp_partial_component_count=0`），因為現行 residual/proxy 建構方式使 stock/bond/cash 目前總是三個一起有值或一起缺值。 | 目前不影響現有資料，但這是程式邏輯本身的潛在風險（brief 10.2 #1、20 節 #1 明確點名），未來資料若出現部分缺值情境會悄悄發生。 | **DECISION-REQUIRED**（已加入 validator 常態監控，見第 6 節） |
| 9 | MEDIUM | `scripts/build_balanced_events.py`（未修改） | Allocation Logic | `has_allocation_change` 用 `.max(axis=1)`（預設 skipna），理論上單一 component 有值就可能被判定為有變化。目前同樣因資料特性使受影響筆數為 **0**（`has_allocation_change_with_partial_components_count=0`）。 | 與 Finding #8 同源同因，目前無資料層級影響，但邏輯風險保留。 | **DOCUMENTED** |
| 10 | MEDIUM | `scripts/build_balanced_events.py`（未修改） | Data Integrity | `other_unclassified_weight` 在 418 列為負值（holdings-proxy 涵蓋率上限設為 1.20，允許加總超過 100%）。 | 「未分類權重」為負在直覺上令人困惑，但這是 `holdings_proxy_max_coverage=1.20` 這個既有設計參數的直接結果，不是計算錯誤。 | **DECISION-REQUIRED** |
| 11 | MEDIUM | `scripts/build_balanced_events.py` + `api_server.py` + `static/app.js`（均未修改） | Financial Methodology / Frontend | `exp_ratio` 缺失時被當作 0% expense（`.fillna(0.0)`），影響 12,792 / 32,907 個 event-level 列（38.9%）。程式已有 `expense_ratio_weight_coverage` 診斷欄位存在於 derived CSV 與 `/api/events` 的全欄位輸出中，但**沒有**被放進 `/api/timeline` 的精選欄位清單，前端 `showDetail()` 的欄位列表也沒有顯示它，因此 dashboard 使用者目前完全看不到哪些列是「假設 0% expense」。 | 不修正基礎公式（brief 11.1 明確要求不要自行改公式），但目前使用者無法從 UI/`/api/timeline` 看出這個假設何時被觸發。 | **DOCUMENTED**（建議：未來把 `expense_ratio_weight_coverage` 加進 timeline 欄位與 detail 顯示；本次未做，避免範圍蔓延） |
| 12 | LOW/INFO | `scripts/build_balanced_events.py`（未修改） | Research Semantics | `family_fallback`（`analysis_entity_level="family_fallback"`）在目前資料中出現次數為 **0**（`family_fallback_pct=0.0`）：只要不是具體 manager，advisor_name 幾乎必定存在，因此永遠落在 `advisor_team_fallback`，`family_fallback` 這條路徑在目前 CRSP 欄位下實務上不可達。 | 非 bug，但論文方法論章節若要描述四層 fallback，應誠實說明第三層目前資料下是空的。 | **INFORMATIONAL** |
| 13 | LOW | `scripts/build_balanced_events.py`（未修改） | Data Integrity | Report-level 欄位（`fund_percent_common_stock/bond/cash`）在同一 `report_dt` 若有多筆 holding rows，目前用 `.agg(..., "first")` 取值，未驗證同一 report 內是否本來就有不一致的重複值。 | 若原始資料在同一 report_dt 下就有欄位不一致（少見但可能），會被 row order 悄悄決定，不會被標記。因需重新處理約 70MB 的原始 holdings CSV，本次 validator（僅讀 derived 檔）未實作此檢查。 | **DOCUMENTED**（建議未來以獨立稽核腳本檢查，不在本次 minimal-fix 範圍） |
| 14 | LOW | `scripts/build_balanced_events.py`（未修改） | Return Logic / Reproducibility | Share-class TNA 加權：`gap_months != 1` 或 lagged TNA 不可用/非正時，回退用**當期（非落後）TNA**。這是同一 event 月內的加權基準選擇，不是跨月份的未來資訊洩漏，但與真正的 temporal leakage 概念不同，故獨立記錄。 | 屬加權基準的研究選擇（可能有輕微的當期報酬-當期規模內生性），非跨時間洩漏。 | **DOCUMENTED** |
| 15 | INFO | `api_server.py` / `static/app.js` | Frontend/D3 | `/api/hierarchy` 只回傳前 100 個 family（依 portfolio 數排序），`renderHierarchy()` 前端再截斷到前 80 列顯示。 | 屬探索用視圖的顯示上限，不影響其餘篩選/圖表使用完整資料。 | **INFORMATIONAL** |
| 16 | INFO | `api_server.py` | FastAPI | `sort=<不存在的欄位>` 時靜默忽略（維持預設順序），不回傳 4xx。 | 與 `limit`/`min_change` 的顯式 422 處理不同調，但屬防禦性设计，不會造成資料錯誤或當機，故不視為需修正的 bug。 | **INFORMATIONAL**（未變更） |
| 17 | LOW | `api_server.py` | Performance/Reproducibility | `event_df()`/`market_df()` 用 `lru_cache(maxsize=1)`，若 server 運行中重新執行 `main.py build`，既有 process 會持續讀取舊快取直到重啟。 | 屬單人本地研究工具的常見取捨，非資料錯誤；本次未加入檔案 mtime 監控等機制以維持改動最小化。 | **DOCUMENTED**（未變更） |

---

## 5. Changes Made

### 5.1 `api_server.py` — 無效日期查詢參數導致 500（Finding #1）

```
File: api_server.py
Function / region: 新增 parse_query_date()；apply_filters()；/api/market
Before:
    if start:
        df = df[df["month_end"] >= pd.Timestamp(start)]
    if end:
        df = df[df["month_end"] <= pd.Timestamp(end)]
After:
    def parse_query_date(value: str, field_name: str) -> pd.Timestamp:
        try:
            ts = pd.Timestamp(value)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"{field_name} must be a valid date (YYYY-MM-DD)") from exc
        if pd.isna(ts):
            raise HTTPException(status_code=422, detail=f"{field_name} must be a valid date (YYYY-MM-DD)")
        return ts
    ...
    if start:
        df = df[df["month_end"] >= parse_query_date(start, "start")]
    if end:
        df = df[df["month_end"] <= parse_query_date(end, "end")]
Why: 無效日期字串（如 ?start=notadate、?end=2020-99-99）先前會讓 pd.Timestamp() 丟出未捕捉的 ValueError，FastAPI 回傳裸的 500。已用同一套模式套用到 apply_filters()（供 /api/events、/api/timeline 使用）與 /api/market。
Research impact: 無；不影響任何研究資料或計算邏輯，純屬 API 輸入驗證。
API/data schema impact: 行為變更 — 無效日期現在回傳 HTTP 422 + {"detail": "...must be a valid date..."}，而非 500。有效日期行為完全不變（已用有效 start/end 組合實測 HTTP 200）。
```

### 5.2 `api_server.py` — Derived 檔案缺失時的未捕捉例外（Finding #2）

```
File: api_server.py
Function / region: create_app() 內新增 exception_handler
Before: 只有 /api/health 用 try/except 包住 event_df()；其餘 6 個 endpoint 完全沒有保護，FileNotFoundError 會直接穿透變成裸的 500。
After:
    @app.exception_handler(FileNotFoundError)
    async def missing_derived_data_handler(request: Request, exc: FileNotFoundError):
        return JSONResponse(status_code=503, content={"ok": False, "error": str(exc)})
Why: 集中處理「尚未執行 python main.py build」這個常見情境，讓所有 endpoint（/api/meta、/api/entities、/api/portfolios、/api/events、/api/timeline、/api/event/{id}、/api/hierarchy、/api/market）都能一致地回報清楚的 503 訊息，而不是隨機出現的裸 500。
Research impact: 無。
API/data schema impact: 僅影響「derived 檔案不存在」這個異常情境下的狀態碼與回應格式（500 → 503，並附帶可讀訊息）；已用「指向不存在的 data_root」實測確認 /api/meta 回傳 503 且訊息正確。/api/health 原有的 500 行為維持不變（其自身邏輯本來就選擇回 500，屬既有設計，未動）。
```

### 5.3 `static/app.js` — 結束月份篩選的時區 off-by-one（Finding #3）

```
File: static/app.js
Function / region: apiParams()
Before:
    if($("#endDate").value){ const [y,m]=$("#endDate").value.split("-").map(Number); p.set("end",new Date(y,m,0).toISOString().slice(0,10)); }
After:
    if($("#endDate").value){ const [y,m]=$("#endDate").value.split("-").map(Number); const lastDay=new Date(Date.UTC(y,m,0)).getUTCDate(); p.set("end",`${y}-${String(m).padStart(2,'0')}-${String(lastDay).padStart(2,'0')}`); }
Why: new Date(y,m,0) 建立的是本地時區的月底午夜，.toISOString() 會轉換到 UTC。對 UTC+ 時區（例如台灣 UTC+8，本專案 UI 語系為 zh-Hant，目標使用者很可能就在此時區）使用者，換算成 UTC 字串會落回前一天（如 2020-03-31 本地 → 2020-03-30 UTC），使 end 篩選少算最後一天，month-end 事件被誤篩掉。改用 Date.UTC() 計算月底天數、再用純字串組出日期，完全不經過本地時區轉換，徹底避開此問題。
Research impact: 無；僅修正前端篩選參數的計算方式，未改動任何後端資料或研究定義。
API/data schema impact: 送給 /api/events、/api/timeline 的 end 參數格式不變（仍是 YYYY-MM-DD），DOM/id 契約無變動。修正後在任何時區下，選擇「結束月份 = 2020-03」都會正確送出 end=2020-03-31。
```

### 5.4 `scripts/validate_step1.py` — Validator 覆蓋率嚴重不足、generic manager 清單與 builder 脫鉤（Finding #4）

```
File: scripts/validate_step1.py
Function / region: 全面擴充（main()）
Before: 僅 6 項檢查（event_id_unique、month_end_is_calendar_month_end、original_report_not_after_month_end、generic_manager_never_labeled_manager_level [用獨立手寫清單]、bond_residual_pass、future_6m_benchmark_pass [僅 benchmark 層級]）。
After: 擴充為 31 項「必須通過」的 deterministic correctness 檢查，外加多項純診斷用統計，涵蓋 brief 第 19 節幾乎全部項目：
  - Identity/uniqueness: event_id_unique、portfolio_month_unique、month_end_is_calendar_month_end、original_report_not_after_month_end
  - Calendar: market_monthly_sequence_complete、market_month_end_unique、fund_return_gap_month_distribution（純報告，非 pass/fail，因為基金報告本身有間隔是正常現象）
  - Manager fallback: generic_manager_never_labeled_manager_level（改為 import scripts.build_balanced_events.GENERIC_MANAGER_NAMES，不再手寫第二份清單）、unresolved_rows_use_unresolved_label、analysis_entity_level_values_known
  - Allocation: 三個 weight 欄位的 sane-range 檢查、bond_residual_pass、other_unclassified_weight_exact（重算 1-known_total 是否精確等於欄位值）、negative count 報告、comparable_change 與 allocation_source/quality 一致性診斷（純報告）
  - Change: prev_month_end_always_a_real_prior_event（新：驗證每個 prev_month_end 都確實對應同一 portfolio 的一個真實觀測列）、gap_months_from_prev_report_exact、delta_stock/bond/cash_pp_exact（獨立重算）、allocation_change_l1_pp_exact、allocation_turnover_pp_exact、partial-component 診斷計數
  - Fund return: gross_return_formula_exact_when_expense_known（新，發現 Finding #5）、gross_equals_net_when_expense_missing、fund_return_csv_unique_by_portfolio_month
  - Benchmark: spxt_total_return_matches_index_pct_change、treasury10y_proxy_return_{d7p5,d8p5,d9p5}_formula_exact、benchmark_70_30_formula_exact、benchmark_method_label_present_and_proxy_disclosed
  - Future 6M: future_6m_benchmark_pass（既有）、future_6m_fund_gross_pass（**新** — 先前完全沒有在 fund 層級驗證過，本次獨立重算 74,653 個 6 個月視窗，最大誤差 1.7e-15）、future_6m_excess_exact（新）、last_6_months_future_6m_unavailable（新，驗證資料尾端最後 6 個月的 outcome 確實是 unavailable，而非被錯誤地用不完整區間算出數值）
  - Merge coverage: 完整回報 holdings-matched-to-fund-return %、holdings-matched-to-market %、allocation_available %、eligible_future_6m %、manager/advisor-fallback/family-fallback/unresolved %
Why: 原始 validator 遠不足以支撐「paper-level data backbone」的宣稱（brief 第 2、19 節），且其 generic manager 清單與 builder 的 GENERIC_MANAGER_NAMES 不同步，是一個真實的一致性風險（brief 第 8 節、20 節 #10 明確點名）。這些新增檢查全部是「對照程式碼自己宣稱的公式重新獨立計算並比較」，沒有引入任何新的研究判斷或 threshold。
Research impact: 無研究定義變更；但驗證覆蓋率的提升**發現了** Finding #5（gross return / expense_ratio_annual 不吻合），這是先前 6 項檢查無法偵測到的真實資料一致性問題。
API/data schema impact: CLI 介面不變（仍是 python scripts/validate_step1.py --data-root data`），輸出檔案位置/檔名不變（data/derived/STEP1_VALIDATION.json`），仍會印到 stdout。JSON 內容從 ~12 個 key 擴充為 ~50 個 key（新增欄位為附加，未刪除任何舊欄位語意；`PASS` 欄位定義從「6 項檢查的 AND」改為「31 項必要檢查的 AND」，且新增 `_required_checks` 陣列列出所有納入 PASS 判定的 key 名稱，讓外部使用者可自行追蹤是哪些檢查決定了 PASS/FAIL）。
```

---

## 6. Changes NOT Made（屬於研究/設計選擇，已發現但刻意不自行修改）

| 主題 | 為何不修改 | 需要的決定 | 建議的穩健性檢查 |
|---|---|---|---|
| Finding #5：`fund_gross_return_approx` 與 `expense_ratio_annual` 在多 share-class 部分缺 exp_ratio 時不吻合 | 至少有兩種合理修法：(a) 讓 `expense_ratio_annual` 的加權也採用「缺值視為 0」的口徑，與 gross return 的既有假設對齊；(b) 反過來讓 gross return 的加權排除掉缺 exp_ratio 的 share class。兩者對「missing expense ratio 如何處理」有不同的研究含義，brief 11.1 節明確要求此類問題不可自行決定。 | 研究者需決定：`expense_ratio_annual` 應該代表「已知 exp_ratio 的加權平均」（目前行為，用於了解真實揭露覆蓋率）還是「與 gross return 假設一致的加權平均」（改為缺值視為 0）。 | 在決定前，任何使用 `expense_ratio_annual` 做為 "expense 覆蓋率" 或反推 gross-net 差異的分析，應先用 `expense_ratio_weight_coverage` 交叉檢查，且應排除或特別標註 `share_class_count > 1` 的列。 |
| Finding #6：`comparable_change`/`event_eligible_6m` 未檢查 bond/cash 與 quality 一致性 | 這是「配置變化的可比較性」該如何定義的研究方法論問題，brief 10.2 節 #3、#5 明確列為已知風險、不可自行假設要改。 | 是否要求 comparable_change 額外滿足：bond/cash 亦非空、且 current/previous 的 `allocation_quality`／`allocation_source` 一致（或至少同屬「reported」或同屬「proxy」）。 | Validator 已新增 `comparable_change_with_differing_allocation_source`（3,527）、`comparable_change_with_differing_allocation_quality`（3,625）、`comparable_change_with_non_high_quality_current_or_prev`（10,842）三項常態監控指標，供決策參考。 |
| Finding #7：`"partial"` 品質層涵蓋率下限 0.40 | `holdings_proxy_min_coverage`/`research_change_threshold_pp` 等屬 `BuildConfig` 中明列、brief 第 6 節明確禁止自行更動的參數。 | 是否要提高 partial 的涵蓋率下限，或改為在 change/eligibility 分析中排除 partial。 | 已用 validator 的 `allocation_quality_counts` 常態追蹤各品質層人數分布。 |
| Finding #8/#9：L1／has_allocation_change 在 component 部分缺值時的行為 | 目前實際受影響筆數為 0，屬於「程式邏輯的潛在風險」而非目前資料的實際問題；若要修，需先決定「部分缺值時 L1 該視為 NaN 還是仍計算」這個定義問題。 | 是否要求 L1／has_allocation_change 只在三個 delta 皆非空時才計算。 | Validator 已加入 `allocation_change_l1_pp_partial_component_count` 與 `has_allocation_change_with_partial_components_count` 做為常態監控，未來資料更新後若此數字不再是 0，會被立即看到。 |
| Finding #10：`other_unclassified_weight` 負值（418 列） | 是 `holdings_proxy_max_coverage=1.20` 這個既有設計參數的直接結果，brief 第 6 節禁止自行調整。 | 是否要將這 418 列標記為 review-only、排除於 magnitude 分析之外。 | Validator 已回報 `other_unclassified_weight_negative_count`。 |
| Finding #11：expense-missing 的 0% 假設在 UI 不可見 | 加入 UI 顯示屬於功能新增而非 bug 修正，brief 強調「一個 code change 只能對應一個 issue」且應避免範圍蔓延；`expense_ratio_weight_coverage` 已存在於 derived CSV 與 `/api/events` 全欄位輸出中，只是未進入 `/api/timeline` 精選欄位與前端 detail 顯示。 | 是否要將 `expense_ratio_weight_coverage` 加入 `/api/timeline` 欄位清單與 `showDetail()` 顯示。 | 目前可透過 `/api/events`（回傳全欄位）取得該欄位做離線分析。 |
| Treasury proxy 為暫時性 duration/carry proxy（非官方 bond total return） | brief 13.2 節明確要求不要自行更換為官方 bond total-return 序列。 | 正式論文應取得官方 Bloomberg/ICE 等 bond total-return index 後替換。 | 現有 sensitivity 欄位（D=7.5/8.5/9.5）與明確的 proxy 標籤已到位，可供未來替換前的 robustness 比較基礎。 |

---

## 7. Validation Results

### 7.1 修改前（原始 validator，6 項檢查）

```json
{
  "event_id_unique": true,
  "month_end_is_calendar_month_end": true,
  "original_report_not_after_month_end": true,
  "generic_manager_never_labeled_manager_level": true,
  "bond_residual_pass": true,
  "future_6m_benchmark_max_abs_error": 1.0547118733938987e-15,
  "future_6m_benchmark_pass": true,
  "event_rows": 32907,
  "date_min": "2002-08-31",
  "date_max": "2025-11-30",
  "PASS": true
}
```

### 7.2 修改後（擴充 validator，31 項必要檢查 + 完整診斷；`python main.py build` 重新產生 derived 資料後再跑 `python scripts/validate_step1.py --data-root data`，資料完全未變：仍是 32,907 rows、2002-08-31 至 2025-11-30）

- **event rows**: 32,907　｜　**unique event IDs**: 32,907　｜　**portfolio-month duplicates**: 0
- **date min/max**: 2002-08-31 – 2025-11-30
- **allocation available**: 17,679 / 32,907（53.7%）
- **allocation quality counts**: `missing`=15,228　`partial`=11,633　`proxy`=4,741　`high`=1,305
- **manager/fallback counts**: `manager`=12,754（38.8%）　`advisor_team_fallback`=20,073（61.0%）　`unresolved`=80（0.24%）　`family_fallback`=0（0%，見 Finding #12）
- **fund-return merge coverage**: 99.70%　｜　**market merge coverage**: 100%
- **change events**（`has_allocation_change`）: 11,409　｜　**research change events**（`is_research_change`，≥0.5pp）: 9,693
- **future 6M eligible**（`event_eligible_6m`）: 10,770（32.7%）
- **future 6M max absolute error**：benchmark 層級 1.05e-15（既有）、**fund 層級 1.72e-15（新，74,653 個視窗獨立重算）**
- **bond residual max error**: 通過（`bond_residual_pass=true`）
- **benchmark formula max error**: SPXT/Treasury/70-30 三項公式重算誤差皆 < 1e-9（機器精度等級）
- **delta recomputation max error**: stock 2.84e-14、bond 1.42e-14、cash 2.13e-14（皆為浮點捨入等級，`*_exact` 皆為 true）

**validation PASS/FAIL：`PASS: false`**

唯一導致 FAIL 的必要檢查：`gross_return_formula_exact_when_expense_known = false`（即 Finding #5）。其餘 30 項必要檢查全數通過，包含本次新增、先前從未驗證過的 `future_6m_fund_gross_pass`（74,653 個視窗、誤差 1.72e-15）。

> **重要澄清**：`PASS` 從 `true` 變成 `false`，**不是**因為本次修改了任何研究邏輯或降低了標準，而是因為擴充後的 validator 終於檢查到一個先前完全沒被測試覆蓋、但確實存在的資料一致性問題（見 Finding #5）。這正是 brief 第 20 節要求的「讓 validator 能正確報出錯誤，而不是只輸出 PASS」。

---

## 8. Temporal Leakage Audit

| Feature | Event month (t) | Report date | Target window | Future information risk | 結果 |
|---|---|---|---|---|---|
| Manager / family / advisor metadata | 取自同一 portfolio-month 內（可能跨多個 share class 的 caldt）的列，deterministic 挑選（優先具體 manager，其次同月內最新 caldt） | 僅限同一 `month_end` 內 | — | 無跨月洩漏；挑選僅發生在同一事件月內部 | **PASS** |
| Allocation weights（stock/bond/cash） | 取自該 portfolio-month 內實際的最後一次 report | `report_date_original` 保留、`report_date_original <= month_end` 恆成立 | — | 無 | **PASS** |
| Share-class TNA 加權 | 當期或落後一期 TNA（同 event 月或前一個月），無使用未來月份 TNA | — | — | 無跨月洩漏；但屬同期加權基準選擇（Finding #14），非洩漏但獨立記錄 | **PASS**（加註） |
| Market month feature（spxt/treasury/70-30） | 與 event 同一 `month_end` merge | — | — | 無 | **PASS** |
| Future 6M outcome（fund 與 benchmark） | 由獨立的 `future_6m_*` 欄位承載，`add_changes()`／`build_all()` 中沒有任何 current-event feature（delta、quality、comparable_change 等）讀取 `future_6m_*` | — | t+1…t+6 | `event_eligible_6m` 依賴 `future_6m_excess_vs_70_30.notna()`，但這只是「目標是否可用」的旗標，不是被當作 feature 使用 | **PASS** |
| 前端 event selection | 篩選條件僅有 entity/portfolio/date/`only_changes`/`min_change`/`quality`，皆為 event-month 當下可得資訊，沒有任何篩選條件讀取 `future_6m_*` 欄位 | — | — | 無 | **PASS** |

**結論：Temporal leakage 紀律全數 PASS**，Step 1 在「不可用未來資訊做為 event feature」這條核心紀律上執行良好，值得在論文方法論章節中明確主張。

---

## 9. Benchmark Audit

- **SPXT = 官方 total-return equity leg**：`spxt_total_return = spxt_index_level.pct_change()`，validator 重新獨立計算後與欄位值誤差 < 1e-9，確認為精確的 index-level 月報酬率。
- **Treasury 10Y = yield，非 total return**：`treasury10y_yield_pct` 直接取自 `DGS10_Yield`，程式從未將其當作 total return 使用。
- **Treasury leg = synthetic duration/carry proxy**：`previous_yield/12 - D × Δyield`，D=8.5（primary）、7.5/9.5（sensitivity），三者公式皆經獨立重算驗證（誤差 < 1e-9）。
- **70/30 = proxy benchmark**：`0.70 × SPXT + 0.30 × Treasury proxy`，公式重算誤差 < 1e-9；`benchmark_method` 欄位對每一列都清楚標註為 "70% SPXT total return + 30% synthetic Treasury duration/carry proxy D=8.5"。
- **多層一致揭露**：`build_config.method_warning`（audit json）、`STEP1_DATA_DICTIONARY.md`、`/api/meta.benchmark_warning`、前端 `method-pill` 橫幅、以及 event detail 中的黃色警語，**四個層面**都一致地標註這是暫時性 proxy、非官方 bond total-return benchmark。**沒有**任何地方誤將其寫成正式 benchmark。

**結論：PASS。** 這是本次審查中執行得特別紮實的一塊——proxy 的暫時性質從資料層、API 層到 UI 層都有一致且不會被使用者忽略的揭露。

---

## 10. Frontend / API Contract Audit

- **9 個文件化 endpoint**（`/`、`/api/health`、`/api/meta`、`/api/entities`、`/api/portfolios`、`/api/events`、`/api/timeline`、`/api/event/{event_id}`、`/api/hierarchy`、`/api/market`）：全數存在、endpoint 名稱未變更。
- **D3 對應欄位**：逐一比對 `/api/timeline` 的精選欄位清單與 `app.js` 實際讀取的欄位（`stock_weight`/`bond_weight`/`cash_weight`、`delta_*_pp`、`fund_gross_return_approx`、`benchmark_70_30_return`、`future_6m_*`、`allocation_source`/`allocation_quality`、`gap_months_from_prev_report` 等），完全一致，無缺欄位風險。
- **日期 filter**：修正 Finding #3 後，任何時區下 `start`/`end` 皆能正確送出對應的月初/月底日期。
- **空資料狀態**：以 `portno=999999999` 實測 `/api/events` 回傳 `200 []`，前端各圖表函式（`drawAllocation`/`drawChanges`/`drawReturns`/`drawScatter`）皆已對「無可用資料」做防禦性判斷並顯示文字提示，不會 crash。
- **單位一致性**：allocation weight 用 0–1 小數配合 `fmtPct()` 顯示為 `%`；delta 用 percentage points 配合 `fmtPP()` 顯示為 `pp`；return 用 decimal 配合 `fmtPct()`；三者在圖表、表格、detail view 中使用一致，未見混淆。
- **Quality/Proxy 揭露**：event 表格與 detail view 皆顯示 `allocation_source`/`allocation_quality`（並用 CSS class `quality-high`/`quality-partial`/`quality-proxy`/`quality-review`/`quality-missing` 上色），70/30 proxy 的警語固定顯示在 detail view 底部。
- **FastAPI ↔ JS contract**：經修正後（Finding #1、#2），無效輸入與缺檔情境都有清楚的 4xx/5xx 回應；有效輸入行為與修正前完全一致（已實測比對）。

**結論：PASS（修正後）。**

---

## 11. Remaining Risks Before Step 2

以下 5 項建議在進入 Step 2（predictive validation）前，由研究者明確裁決：

1. **Finding #5**（gross return / expense_ratio_annual 不吻合）：任何用 `fund_gross_return_approx` 或 `expense_ratio_annual` 做為 feature／控制變數的分析，應先決定其中一個欄位的口徑修正方式，否則多 share-class 基金的費用假設會有內部不一致。
2. **Finding #6**（`comparable_change`/`event_eligible_6m` 未檢查 quality/source 一致性）：在用「配置變化幅度 → 未來超額報酬」做任何預測性檢定之前，需先決定是否要求 quality/source 一致，否則約半數以上的「可比較變化」樣本混雜了資料品質切換。
3. **Finding #7**（partial 品質層佔多數）：需決定 partial-quality 觀測值是否納入正式的 event 母體，或僅作為敏感度分析的擴充樣本。
4. **Treasury proxy 暫時性**：正式論文用的 baseline 模型應在取得官方 bond total-return 序列後重新驗證 70/30 benchmark 的穩健性。
5. **Finding #10**（418 列 `other_unclassified_weight` 為負）：需決定是否將這些列排除於 allocation-magnitude 相關分析之外。

---

## 12. Explicitly Out of Scope

本次審查與修改**未**進行以下項目（符合 brief 第 6 節禁止事項）：

```text
No XGBoost added
No SHAP added
No LLM / RAG / MCP added
No Step 2 predictive metrics added
No Equity Fund pipeline added
No change to research_change_threshold_pp / max_change_gap_months / holdings_proxy_min_coverage / holdings_proxy_max_coverage / treasury duration parameters
No change to raw CSV or derived CSV content semantics
No change to requirements.txt / pyproject.toml / uv.lock / .gitignore
No new files created besides this changelog
```

---

## 13. Acceptance Criteria 對照

| 項目 | 狀態 |
|---|---|
| Event ID 唯一 | ✅ PASS |
| 一個 portfolio-month 最多一個 observed event | ✅ PASS |
| 原始 report date 保留 | ✅ PASS |
| month_end 無 timezone/off-by-one 錯誤（**後端**；前端已修正見 Finding #3） | ✅ PASS |
| 不製造 missing-month events | ✅ PASS |
| generic manager 不被當成 individual manager | ✅ PASS（且清單已與 builder 同步，見 Finding #4） |
| bond residual 僅在 stock+cash 可用時計算 | ✅ PASS |
| allocation source/quality 可追溯 | ✅ PASS |
| incomplete holdings 未被硬 normalize 到 100% | ✅ PASS |
| delta 計算可重算一致 | ✅ PASS |
| report gap 正確 | ✅ PASS |
| share-class aggregation 可複現 | ⚠️ 可複現，但 `expense_ratio_annual` 與 gross return 的內部一致性有 Finding #5 |
| expense ratio 缺值不被誤解為已知 0% | ⚠️ 程式邏輯上是刻意假設（有註記），但 UI 目前未顯示每列的 `expense_ratio_weight_coverage`，見 Finding #11 |
| SPXT monthly return 可由 level 精確重算 | ✅ PASS |
| Treasury yield 未被直接當 total return | ✅ PASS |
| 70/30 proxy 公式可精確重算 | ✅ PASS |
| market calendar gap 已驗證 | ✅ PASS（新增） |
| fund Future 6M = t+1…t+6 calendar months | ✅ PASS（新增驗證） |
| benchmark Future 6M = t+1…t+6 calendar months | ✅ PASS |
| Future 6M 使用複利 | ✅ PASS |
| fund 與 benchmark horizon 完全一致 | ✅ PASS |
| excess 計算正確 | ✅ PASS |
| target 不會回流 current feature | ✅ PASS |
| validator 能正確報出錯誤，而非只輸出 PASS | ✅ PASS（本次擴充後，validator 已實際抓到 Finding #5 並回報 `PASS:false`） |
| FastAPI endpoints 與 D3 frontend contract 一致 | ✅ PASS |
| frontend date filtering 無 timezone bug | ✅ PASS（已修正） |
| empty/missing data 不會讓 frontend crash | ✅ PASS |
| `CLAUDE_STEP1_REVIEW_AND_CHANGELOG.md` 詳細記錄所有修改與未修改風險 | ✅ 本文件 |

---

## 14. 總結

Step 1 的**核心研究骨架是穩健的**：event identity、月曆定義、manager fallback、bond residual、70/30 proxy 的暫時性揭露、以及最重要的 temporal leakage 紀律，在程式碼層級都通過了嚴格的獨立重算驗證。本次修正的 4 個 bug（API 錯誤處理、前端時區、validator 覆蓋率）都與研究方法論無關，純屬「介面與驗證工具的正確性」問題。

本次審查最有價值的產出，是把 validator 從 6 項檢查擴充到 31 項必要檢查，並藉此**新發現**一個先前完全不可見的資料一致性問題（Finding #5），以及**量化**了數個先前只是「懷疑」但沒有數字佐證的已知風險（Finding #6、#7、#8、#9、#10 都附上了精確筆數）。這些發現已完整記錄於本報告，尚未被自動修改——是否調整、如何調整，留給研究者依論文的具體主張來決定。
