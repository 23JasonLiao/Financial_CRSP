# Input data placement

Place your existing files under these exact canonical names:

```text
data/
├── crsp/
│   ├── fund_level/
│   │   ├── balanced_before2010.csv
│   │   └── balanced_after2010.csv
│   └── holdings_raw/
│       ├── stock berfore 2010_new___.csv
│       ├── stock between 2010_2014_new___.csv
│       ├── stock between 2015_2019_new___.csv
│       └── stock between 2020_2026_new___.csv
├── market/
│   ├── spxt_index_1997_2025.csv
│   └── treasury_10y_1997_2025.csv
└── part5_non_individual_holdings/
    ├── part5_excluded_two_group_enriched.csv
    └── part5_excluded_individual_stock_like_removed_audit.csv
```

The other Part 5 files can remain in your project, but Step 1 does not need them yet.
