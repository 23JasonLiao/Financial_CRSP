import pandas as pd

# 1. 抓取 FRED 的 10 年期公債殖利率
url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
df = pd.read_csv(url)

# 2. 強制將日期欄位轉回 YYYY-MM-DD，數值無法轉換的(如 ".") 設為空值並踢除
df['observation_date'] = pd.to_datetime(df['observation_date'])
df['DGS10'] = pd.to_numeric(df['DGS10'], errors='coerce')
df = df.dropna().sort_values('observation_date', ascending=True)

# 3. 篩選 1997-12-31 到 2025-12-31
mask = (df['observation_date'] >= '1997-12-31') & (df['observation_date'] <= '2025-12-31')
df_filtered = df.loc[mask].copy()

# 4. 以「月底」為單位，取當月最後一個有效交易日的殖利率
df_monthly = df_filtered.groupby(df_filtered['observation_date'].dt.to_period('M')).last().reset_index(drop=True)

# 5. 格式化欄位名稱
df_monthly.columns = ['Date', 'DGS10_Yield']
df_monthly['Date'] = df_monthly['Date'].dt.strftime('%Y-%m-%d')

# 6. 匯出成乾淨的 CSV
df_monthly.to_csv('treasury_10y_1997_2025.csv', index=False)

print(f"成功匯出！共 {len(df_monthly)} 個月份。")
print("前 5 筆資料如下 (第一筆已對齊為 1997-12-31)：")
print(df_monthly.head())