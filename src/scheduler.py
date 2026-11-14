import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from src import pipeline
from src.detection import scoring
from src.generator import generate

Path("logs").mkdir(exist_ok=True)
log = logging.getLogger("scheduler")
logging.getLogger().addHandler(logging.FileHandler("logs/scheduler.log", encoding="utf-8"))


def run_cycle(args):
    t0 = time.perf_counter()
    log.info("cycle started")

    if not args.no_generate:
        path, raw, labels = generate.run(n=args.batch_size, fraud_rate=args.fraud_rate,
                                         minutes=args.window_minutes)
        log.info("generated %s (%d rows, %d fraud)", path.name, len(raw),
                 int(labels.is_fraud.sum()))

    pipeline.main()    # clean + load every file waiting in data/incoming/
    scoring.main()     # features, rules, anomaly score, alerts

    log.info("cycle finished in %.1fs", time.perf_counter() - t0)


def main():
    p = argparse.ArgumentParser(description="Run the fraud pipeline on a schedule.")
    p.add_argument("--every", type=float, default=1, help="minutes between cycles")
    p.add_argument("--batch-size", type=int, default=1500, help="normal txns per live batch")
    p.add_argument("--window-minutes", type=int, default=180)
    p.add_argument("--fraud-rate", type=float, default=0.02)
    p.add_argument("--no-generate", action="store_true",
                   help="only process files already in data/incoming/ (real-feed mode)")
    p.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = p.parse_args()

    if args.once:
        run_cycle(args)
        return

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(run_cycle, "interval", minutes=args.every, args=[args],
                      id="cycle", max_instances=1, coalesce=True,
                      misfire_grace_time=60, next_run_time=datetime.now())
    log.info("scheduler running: every %s min (Ctrl+C to stop)", args.every)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()