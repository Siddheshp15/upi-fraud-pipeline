from sqlalchemy import text


def start_run(conn, source_file):
    return conn.execute(
        text("INSERT INTO pipeline_runs (source_file) VALUES (:f) RETURNING run_id"),
        {"f": source_file}).scalar()


def load_raw(conn, raw, run_id, source_file):
    df = raw.copy()
    df["run_id"] = run_id
    df["source_file"] = source_file
    df.to_sql("raw_transactions", conn, if_exists="append", index=False,
              chunksize=2000, method="multi")


def load_clean(conn, clean_df, run_id):
    """Idempotent: rows whose txn_id already exists are skipped.
    Returns the number of rows actually inserted."""
    if clean_df.empty:
        return 0
    clean_df.to_sql("stg_clean_batch", conn, if_exists="replace", index=False,
                    chunksize=2000, method="multi")
    result = conn.execute(text("""
        INSERT INTO clean_transactions
            (txn_id, txn_ts, sender_upi, receiver_upi, amount, status,
             device_id, location, run_id)
        SELECT txn_id, txn_ts, sender_upi, receiver_upi,
               CAST(amount AS NUMERIC(12,2)), status, device_id, location, :run_id
        FROM stg_clean_batch
        ON CONFLICT (txn_id) DO NOTHING
    """), {"run_id": run_id})
    conn.execute(text("DROP TABLE stg_clean_batch"))
    return result.rowcount


def load_rejected(conn, rejected_df, run_id, source_file):
    if rejected_df.empty:
        return
    params = [{"run_id": run_id, "src": source_file,
               "raw": r.raw_data, "reason": r.reject_reason}
              for r in rejected_df.itertuples()]
    conn.execute(text("""
        INSERT INTO rejected_rows (run_id, source_file, raw_data, reject_reason)
        VALUES (:run_id, :src, CAST(:raw AS JSONB), :reason)
    """), params)


def finish_run(conn, run_id, stats, status):
    conn.execute(text("""
        UPDATE pipeline_runs SET finished_at = now(), rows_in = :rows_in,
            rows_cleaned = :rows_cleaned, rows_rejected = :rows_rejected,
            duplicates_removed = :duplicates_removed,
            duration_seconds = :duration_seconds, status = :status
        WHERE run_id = :run_id
    """), {
        "rows_in": stats["rows_in"], "rows_cleaned": stats["rows_cleaned"],
        "rows_rejected": stats["rows_rejected"],
        "duplicates_removed": stats["duplicates_removed"],
        "duration_seconds": stats["duration_seconds"],
        "status": status, "run_id": run_id,
    })


def fail_run(conn, run_id):
    conn.execute(text("UPDATE pipeline_runs SET status = 'failed', finished_at = now() "
                      "WHERE run_id = :r"), {"r": run_id})