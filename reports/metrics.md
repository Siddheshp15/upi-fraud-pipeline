# Detection evaluation

_Generated 2026-11-14 10:54. Ground truth = synthetic fraud injected by my own generator, so results show how well the system finds those patterns, not how it would perform on real UPI fraud._

## Headline (alerts in the alerts table)

| metric | value |
|---|---|
| Transactions evaluated | 99,012 |
| Real fraud transactions | 1,907 |
| Alerts raised | 1,729 |
| True positives | 1676 |
| False positives | 53 |
| Missed fraud | 231 |
| Precision % | 96.9 |
| Recall % | 87.9 |
| F1 % | 92.2 |
| False positive rate % | 0.055 |

## Precision at K (ranking quality)

| K | fraud_in_top_K | precision_% |
|---|---|---|
| 50 | 50 | 100.00 |
| 100 | 100 | 100.00 |
| 200 | 200 | 100.00 |

## Recall per fraud pattern

| fraud_type | fraud_txns | caught | recall_% |
|---|---|---|---|
| velocity | 620 | 389 | 62.74 |
| amount_spike | 50 | 50 | 100.00 |
| failed_then_success | 252 | 252 | 100.00 |
| device_location_change | 50 | 50 | 100.00 |
| mule | 639 | 639 | 100.00 |
| new_payee_high | 50 | 50 | 100.00 |
| odd_hours | 50 | 50 | 100.00 |
| round_amount | 196 | 196 | 100.00 |

## Precision per rule

| rule | alerts | true_fraud | precision_% |
|---|---|---|---|
| amount_spike | 1048 | 1046 | 99.81 |
| mule_fan_in | 603 | 589 | 97.68 |
| velocity | 371 | 371 | 100.00 |
| new_payee_high | 289 | 287 | 99.31 |
| failed_then_success | 247 | 247 | 100.00 |
| round_amount | 196 | 196 | 100.00 |
| mule_outflow | 73 | 50 | 68.49 |
| odd_hours | 60 | 58 | 96.67 |
| device_location_change | 50 | 50 | 100.00 |
| (anomaly only) | 28 | 18 | 64.29 |

## Rules vs IsolationForest vs combined

| approach | precision@100_% | precision@1907_% | avg_precision | roc_auc |
|---|---|---|---|---|
| Rules only | 100.00 | 86.94 | 0.86 | 0.93 |
| IsolationForest only | 100.00 | 80.18 | 0.87 | 0.99 |
| Rules + IsolationForest | 100.00 | 93.60 | 0.98 | 1.00 |

## Risk threshold trade-off (combined score)

| risk_threshold | alerts | precision_% | recall_% | false_positive_rate_% |
|---|---|---|---|---|
| 10 | 66015 | 2.89 | 100.00 | 66.02 |
| 20 | 33038 | 5.77 | 100.00 | 32.06 |
| 30 | 1701 | 97.47 | 86.94 | 0.04 |
| 40 | 1700 | 97.47 | 86.89 | 0.04 |
| 50 | 1632 | 97.49 | 83.43 | 0.04 |
| 60 | 1025 | 98.54 | 52.96 | 0.02 |
| 70 | 1002 | 99.00 | 52.02 | 0.01 |

## Pipeline runs: data quality and throughput

| run_id | source_file | rows_in | rows_cleaned | rows_rejected | duplicates_removed | duration_seconds | cleaned_% | rejected_% | duplicates_% | rows_per_sec |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | transactions_20261114_105334.csv | 103996 | 99012 | 2945 | 2039 | 33.04 | 95.21 | 2.83 | 1.96 | 3147.58 |

### Rejection reasons

| reject_reason | rows |
|---|---|
| invalid_receiver_upi | 984 |
| invalid_sender_upi | 983 |
| non_positive_amount | 490 |
| future_timestamp | 462 |
| invalid_sender_upi;invalid_receiver_upi | 7 |
| non_positive_amount;invalid_receiver_upi | 5 |
| future_timestamp;invalid_sender_upi | 5 |
| non_positive_amount;invalid_sender_upi | 5 |
| future_timestamp;invalid_receiver_upi | 4 |