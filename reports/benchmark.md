# Query performance: before vs after indexes

_Generated 2026-11-13 21:27. Local PostgreSQL, 1,000,000 synthetic transactions and 20,116 alerts spread over 30 days; queries filter the last 7 days. Median of 7 runs after 1 warm-up. 'Before' = primary keys only. Rows were generated directly in SQL for load testing; this measures query speed, not detection quality._

## Results

| query | before_ms | after_ms | speedup_x | plan_before | plan_after |
|---|---|---|---|---|---|
| Overview KPIs (7 days) | 117.7 | 61.4 | 1.9 | Seq Scan on clean_transactions, Seq Scan on alerts | Index Scan on clean_transactions, Seq Scan on alerts |
| Daily trend (7 days) | 187.1 | 207.3 | 0.9 | Seq Scan on clean_transactions, Seq Scan on alerts | Index Scan on clean_transactions, Seq Scan on alerts |
| Alerts queue: top 200 open | 111.5 | 6.9 | 16.0 | Seq Scan on clean_transactions, Seq Scan on alerts | Index Scan on alerts, Index Scan on clean_transactions |
| Alerts queue + rule filter | 16.3 | 11.6 | 1.4 | Seq Scan on alerts, Index Scan on clean_transactions | Index Scan on alerts, Index Scan on clean_transactions |
| Riskiest accounts (7 days) | 125.6 | 55.4 | 2.3 | Seq Scan on clean_transactions, Seq Scan on alerts | Index Scan on clean_transactions, Seq Scan on alerts |
| Account lookup (analyst drill-down) | 65.8 | 0.4 | 146.6 | Index Scan on clean_transactions, Seq Scan on clean_transactions | Index Scan on clean_transactions |

**One full dashboard refresh (the 5 dashboard queries): 558 ms -> 343 ms (1.6x faster).**

Index build time: 8.8s.

## Storage cost of the indexes

|  | before | after |
|---|---|---|
| transactions table (data only) | 115 MB | 115 MB |
| transactions incl. indexes | 146 MB | 249 MB |
| alerts incl. indexes | 3304 kB | 3992 kB |