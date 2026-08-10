import pandas as pd

# 1. 讀取你從 Google Sheets 下載下來的 .xlsx 檔案
input_excel = 'sp500tr_raw.xlsx'  # 請修改為你的 Excel 實際檔名
output_csv = 'spxt_index_1997_2025.csv'

# 讀取 Excel 內容
df = pd.read_excel(input_excel)

# 2. 清理日期欄位 (移除 " 下午 4:00:00" 或 " 上午 12:00:00" 字串)
df['Date'] = df['Date'].astype(str)
df['Date'] = df['Date'].str.replace(' 下午 4:00:00', '', regex=False)
df['Date'] = df['Date'].str.replace(' 上午 12:00:00', '', regex=False)

# 轉為 datetime 物件與數字型態
df['Date'] = pd.to_datetime(df['Date'])
df['Close'] = pd.to_numeric(df['Close'], errors='coerce')
df = df.dropna().sort_values('Date')

# 3. 抓取每月最後一個交易日 (Resample to Monthly Last)
df_monthly = df.groupby(df['Date'].dt.to_period('M')).last().reset_index(drop=True)

# 4. 格式化日期與欄位名稱
df_monthly['Date'] = df_monthly['Date'].dt.strftime('%Y-%m-%d')
df_monthly.columns = ['Date', 'PX_LAST']

# 5. 匯出成 CSV 檔案
df_monthly.to_csv(output_csv, index=False)

print(f"處理完成！已成功儲存為 {output_csv}。共 {len(df_monthly)} 個月份資料。")
print("\n前 5 筆資料預覽 (包含 1997-12-31 底期)：")
print(df_monthly.head())