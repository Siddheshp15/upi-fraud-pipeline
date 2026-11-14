import logging
import time

from sqlalchemy import create_engine

from src.config import DB_URL, PROCESSED
from src.etl import extract, load, transform

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


def run_batch(path, engine):
    t0 = time.perf_counter()

    # the run row is committed first, so a failed batch still leaves a record
    with engine.begin() as conn:
        run_id = load.start_run(conn, path.name)

    try:
        # everything below is ONE transaction: all or nothing
        with engine.begin() as conn:
            raw = extract.read_file(path)
            load.load_raw(conn, raw, run_id, path.name)

            clean_df, rejected_df, stats = transform.clean(raw)

            inserted = load.load_clean(conn, clean_df, run_id)
            stats["duplicates_removed"] += len(clean_df) - inserted  # already in DB
            stats["rows_cleaned"] = int(inserted)

            load.load_rejected(conn, rejected_df, run_id, path.name)

            stats["duration_seconds"] = round(time.perf_counter() - t0, 2)
            load.finish_run(conn, run_id, stats, "success")
    except Exception:
        with engine.begin() as conn:
            load.fail_run(conn, run_id)
        log.exception("Batch failed: %s", path.name)
        raise

    path.replace(PROCESSED / path.name)
    rate = stats["rows_in"] / max(stats["duration_seconds"], 0.01)
    log.info("%s | in=%d cleaned=%d rejected=%d dups=%d | %.1fs (%.0f rows/s)",
             path.name, stats["rows_in"], stats["rows_cleaned"], stats["rows_rejected"],
             stats["duplicates_removed"], stats["duration_seconds"], rate)
    return stats


def main():
    PROCESSED.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DB_URL)
    files = extract.list_new_files()
    if not files:
        log.info("No new files in data/incoming/")
        return
    for f in files:
        run_batch(f, engine)


if __name__ == "__main__":
    main()