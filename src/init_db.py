import argparse
from sqlalchemy import create_engine, text
from src.config import DB_URL

TABLES = ["alerts", "user_features", "rejected_rows",
          "clean_transactions", "raw_transactions", "pipeline_runs"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true",
                        help="drop all tables first (deletes all data)")
    args = parser.parse_args()

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        if args.reset:
            for t in TABLES:
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t} CASCADE")
            print("Dropped existing tables.")
        for sql_file in ("sql/01_schema.sql", "sql/02_indexes.sql"):
            with open(sql_file, encoding="utf-8") as f:
                conn.exec_driver_sql(f.read())

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        )).fetchall()
    print("Tables in database:")
    for (name,) in rows:
        print(" -", name)

if __name__ == "__main__":
    main()