import sys
from pathlib import Path

# let `streamlit run dashboard/app.py` find the src package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text
from streamlit_autorefresh import st_autorefresh

from src.config import DB_URL
from src.detection.rules import RULES

IST = "Asia/Kolkata"
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

st.set_page_config(page_title="UPI Fraud Monitor", page_icon="🛡️", layout="wide")


# ------------------------------------------------------------------ database helpers
@st.cache_resource
def get_engine():
    return create_engine(DB_URL, pool_pre_ping=True)


@st.cache_data(ttl=30, show_spinner=False)
def query(sql, params=None):
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def run_write(sql, params):
    with get_engine().begin() as conn:
        conn.execute(text(sql), params)
    st.cache_data.clear()


def to_float(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(float)      # NUMERIC columns arrive as Decimal
    return df


def in_list(rules, name):
    return name in (rules or [])


# Filters shared by the alerts queue and the pattern view
ALERT_FILTER = """
    c.txn_ts >= :t_from AND c.txn_ts < :t_to
    AND a.risk_score >= :min_risk
    AND c.amount >= :min_amount
    AND (cardinality(CAST(:rules AS text[])) = 0
         OR a.rules_triggered && CAST(:rules AS text[]))
"""

# ------------------------------------------------------------------ date bounds
bounds = query("SELECT min(txn_ts) AS lo, max(txn_ts) AS hi FROM clean_transactions")
if bounds["lo"].isna().all():
    st.warning("No data yet. Run the pipeline first: python -m src.pipeline")
    st.stop()
lo = pd.to_datetime(bounds["lo"].iloc[0], utc=True).tz_convert(IST).date()
hi = pd.to_datetime(bounds["hi"].iloc[0], utc=True).tz_convert(IST).date()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Filters")
    picked = st.date_input("Date range (IST)", value=(lo, hi), min_value=lo, max_value=hi)
    min_risk = st.slider("Minimum risk score", 0, 100, 0)
    min_amount = st.number_input("Minimum amount (₹)", min_value=0, value=0, step=1000)
    rules_sel = st.multiselect("Rules triggered (any of)", list(RULES))
    st.caption("Date and risk score apply everywhere. Amount and rule filters apply "
               "to the Alerts queue and Patterns tabs.")
    st.divider()
    auto = st.checkbox("Auto-refresh every 30 s", value=True)
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

if auto:
    st_autorefresh(interval=30_000, key="auto_refresh")

if not (isinstance(picked, (tuple, list)) and len(picked) == 2):
    st.info("Pick an end date to continue.")
    st.stop()

d0, d1 = picked
t_from = pd.Timestamp(d0, tz=IST).to_pydatetime()
t_to = (pd.Timestamp(d1, tz=IST) + pd.Timedelta(days=1)).to_pydatetime()
base = {"t_from": t_from, "t_to": t_to, "min_risk": float(min_risk)}
alert_params = {**base, "min_amount": float(min_amount), "rules": list(rules_sel)}

st.title("🛡️ UPI Fraud Monitor")
st.caption("Synthetic data only. Results reflect the fraud patterns injected by my own generator, "
           "not real-world UPI fraud.")

tab1, tab2, tab3, tab4 = st.tabs(["Overview", "Alerts queue", "Patterns", "Data quality"])

# ------------------------------------------------------------------ tab 1: overview
with tab1:
    kpi = query("""
        SELECT count(*) AS txns,
               count(a.alert_id) FILTER (WHERE a.risk_score >= :min_risk) AS flagged,
               coalesce(sum(c.amount) FILTER (WHERE a.risk_score >= :min_risk
                                              AND c.status = 'SUCCESS'), 0) AS at_risk,
               count(a.alert_id) FILTER (WHERE a.risk_score >= :min_risk
                                         AND a.status = 'open') AS open_alerts
        FROM clean_transactions c
        LEFT JOIN alerts a ON a.txn_id = c.txn_id
        WHERE c.txn_ts >= :t_from AND c.txn_ts < :t_to
    """, base).iloc[0]

    txns, flagged = int(kpi["txns"]), int(kpi["flagged"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Transactions", f"{txns:,}")
    c2.metric("Flagged", f"{flagged:,}",
              f"{100 * flagged / txns:.2f}% of transactions" if txns else None,
              delta_color="off")
    c3.metric("Amount at risk", f"₹{float(kpi['at_risk']):,.0f}",
              help="Sum of successful flagged transactions. Failed ones moved no money.")
    c4.metric("Open alerts", f"{int(kpi['open_alerts']):,}")

    daily = query("""
        SELECT date_trunc('day', c.txn_ts AT TIME ZONE 'Asia/Kolkata') AS day,
               count(*) AS txns,
               count(a.alert_id) FILTER (WHERE a.risk_score >= :min_risk) AS alerts
        FROM clean_transactions c
        LEFT JOIN alerts a ON a.txn_id = c.txn_id
        WHERE c.txn_ts >= :t_from AND c.txn_ts < :t_to
        GROUP BY 1 ORDER BY 1
    """, base)

    left, right = st.columns(2)
    left.subheader("Transactions per day")
    left.plotly_chart(px.line(daily, x="day", y="txns", markers=True,
                              labels={"day": "Day (IST)", "txns": "Transactions"}))
    right.subheader("Alerts per day")
    right.plotly_chart(px.bar(daily, x="day", y="alerts",
                              labels={"day": "Day (IST)", "alerts": "Alerts"}))

# ------------------------------------------------------------------ tab 2: alerts queue
with tab2:
    f1, f2 = st.columns(2)
    status_f = f1.radio("Status", ["open", "reviewed", "all"], horizontal=True)
    top_n = f2.number_input("Show top N alerts", min_value=10, max_value=2000,
                            value=200, step=50)

    df = query(f"""
        SELECT a.alert_id, a.risk_score, a.rules_triggered, a.status,
               c.txn_id, c.txn_ts, c.sender_upi, c.receiver_upi, c.amount,
               c.status AS txn_status, c.location
        FROM alerts a JOIN clean_transactions c ON c.txn_id = a.txn_id
        WHERE {ALERT_FILTER}
          AND (:status = 'all' OR a.status = :status)
        ORDER BY a.risk_score DESC, c.txn_ts DESC
        LIMIT :lim
    """, {**alert_params, "status": status_f, "lim": int(top_n)})

    if df.empty:
        st.info("No alerts match these filters.")
    else:
        df = to_float(df, ["risk_score", "amount"])
        view = pd.DataFrame({
            "alert_id": df["alert_id"],
            "risk_score": df["risk_score"],
            "why flagged": df["rules_triggered"].map(lambda r: ", ".join(r or [])),
            "amount": df["amount"],
            "time (IST)": pd.to_datetime(df["txn_ts"], utc=True)
                            .dt.tz_convert(IST).dt.strftime("%d %b %H:%M:%S"),
            "sender": df["sender_upi"],
            "receiver": df["receiver_upi"],
            "txn status": df["txn_status"],
            "location": df["location"],
            "alert status": df["status"],
            "txn_id": df["txn_id"],
        })

        st.caption(f"Showing {len(view):,} alerts, highest risk first. "
                   "Tick rows on the left to select them.")
        event = st.dataframe(
            view, hide_index=True, on_select="rerun", selection_mode="multi-row",
            key="alerts_table",
            column_config={
                "risk_score": st.column_config.ProgressColumn(
                    "Risk", min_value=0, max_value=100, format="%.0f"),
                "amount": st.column_config.NumberColumn("Amount (₹)", format="%.2f"),
            })

        picked_rows = event.selection.rows
        ids = [int(i) for i in view.iloc[picked_rows]["alert_id"].tolist()]

        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("Mark selected as reviewed", disabled=not ids):
            run_write("UPDATE alerts SET status = 'reviewed', reviewed_at = now() "
                      "WHERE alert_id = ANY(:ids)", {"ids": ids})
            st.rerun()
        if b2.button("Reopen selected", disabled=not ids):
            run_write("UPDATE alerts SET status = 'open', reviewed_at = NULL "
                      "WHERE alert_id = ANY(:ids)", {"ids": ids})
            st.rerun()
        b3.download_button("Download shown alerts (CSV)",
                           view.to_csv(index=False).encode("utf-8"),
                           file_name="alerts.csv", mime="text/csv")

# ------------------------------------------------------------------ tab 3: patterns
with tab3:
    pat = query(f"""
        SELECT c.txn_ts, c.sender_upi, c.receiver_upi, c.amount,
               a.risk_score, a.rules_triggered
        FROM alerts a JOIN clean_transactions c ON c.txn_id = a.txn_id
        WHERE {ALERT_FILTER}
    """, alert_params)

    if pat.empty:
        st.info("No alerts match these filters.")
    else:
        pat = to_float(pat, ["amount", "risk_score"])
        pat["ts"] = pd.to_datetime(pat["txn_ts"], utc=True).dt.tz_convert(IST)

        heat = (pat.groupby([pat["ts"].dt.dayofweek, pat["ts"].dt.hour]).size()
                   .unstack(fill_value=0)
                   .reindex(index=range(7), columns=range(24), fill_value=0))
        heat.index = DAY_NAMES

        h1, h2 = st.columns([3, 2])
        h1.subheader("When do alerts happen? (IST)")
        h1.plotly_chart(px.imshow(
            heat, aspect="auto", color_continuous_scale="Reds",
            labels=dict(x="Hour of day", y="Day", color="Alerts")))

        by_rule = (pat.explode("rules_triggered")["rules_triggered"]
                      .value_counts().rename_axis("rule").reset_index(name="alerts"))
        h2.subheader("Alerts per rule")
        h2.plotly_chart(px.bar(by_rule, x="alerts", y="rule", orientation="h")
                          .update_layout(yaxis={"categoryorder": "total ascending"}))

        st.subheader("Riskiest accounts (as sender)")
        risky = (pat.groupby("sender_upi")
                    .agg(alerts=("risk_score", "size"),
                         max_risk=("risk_score", "max"),
                         total_amount=("amount", "sum"))
                    .sort_values(["alerts", "max_risk"], ascending=False)
                    .head(15).reset_index())
        st.dataframe(risky, hide_index=True, column_config={
            "max_risk": st.column_config.NumberColumn("Max risk", format="%.0f"),
            "total_amount": st.column_config.NumberColumn("Total amount (₹)", format="%.0f"),
        })

        st.subheader("Suspected mule accounts")
        st.caption("Accounts that received money from many different senders within an hour "
                   "(fan-in), and how much they sent out soon afterwards.")
        fan = pat[pat["rules_triggered"].map(lambda r: in_list(r, "mule_fan_in"))]
        if fan.empty:
            st.info("No mule fan-in alerts in this selection.")
        else:
            inflow = fan.groupby("receiver_upi").agg(
                senders=("sender_upi", "nunique"),
                received=("amount", "sum"),
                first_seen=("ts", "min"))
            outflow = (pat[pat["rules_triggered"].map(lambda r: in_list(r, "mule_outflow"))]
                       .groupby("sender_upi").agg(sent_out=("amount", "sum")))
            mule = inflow.join(outflow, how="left").fillna({"sent_out": 0})
            mule["first_seen"] = mule["first_seen"].dt.strftime("%d %b %H:%M")
            mule = (mule.sort_values("received", ascending=False)
                        .reset_index().rename(columns={"receiver_upi": "account"}))
            st.dataframe(mule, hide_index=True, column_config={
                "received": st.column_config.NumberColumn("Received (₹)", format="%.0f"),
                "sent_out": st.column_config.NumberColumn("Sent out (₹)", format="%.0f"),
            })

# ------------------------------------------------------------------ tab 4: data quality
with tab4:
    runs = query("""
        SELECT run_id, source_file, started_at, rows_in, rows_cleaned, rows_rejected,
               duplicates_removed, duration_seconds, status
        FROM pipeline_runs ORDER BY run_id DESC LIMIT 50
    """)
    if runs.empty:
        st.info("No pipeline runs yet.")
    else:
        runs = to_float(runs, ["duration_seconds"])
        ok = runs[runs["status"] == "success"].copy()
        if not ok.empty:
            tot = max(int(ok["rows_in"].sum()), 1)
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Rows ingested", f"{int(ok['rows_in'].sum()):,}")
            k2.metric("Cleaned", f"{100 * ok['rows_cleaned'].sum() / tot:.1f}%")
            k3.metric("Rejected", f"{100 * ok['rows_rejected'].sum() / tot:.1f}%")
            k4.metric("Duplicates", f"{100 * ok['duplicates_removed'].sum() / tot:.1f}%")
            k5.metric("Throughput",
                      f"{ok['rows_in'].sum() / max(ok['duration_seconds'].sum(), 0.01):,.0f} rows/s")

            ok["run"] = "run " + ok["run_id"].astype(str)
            chart = ok.sort_values("run_id").melt(
                id_vars="run", value_vars=["rows_cleaned", "rows_rejected", "duplicates_removed"],
                var_name="outcome", value_name="rows")
            st.subheader("Outcome per pipeline run")
            st.plotly_chart(px.bar(chart, x="run", y="rows", color="outcome", barmode="stack"))

        reasons = query("""
            SELECT reason, count(*) AS n
            FROM rejected_rows, unnest(string_to_array(reject_reason, ';')) AS reason
            GROUP BY reason ORDER BY n DESC
        """)
        if not reasons.empty:
            st.subheader("Why rows were rejected")
            st.plotly_chart(px.bar(reasons, x="n", y="reason", orientation="h",
                                   labels={"n": "Rows", "reason": ""})
                              .update_layout(yaxis={"categoryorder": "total ascending"}))

        st.subheader("Run log")
        runs["started (IST)"] = (pd.to_datetime(runs["started_at"], utc=True)
                                   .dt.tz_convert(IST).dt.strftime("%d %b %H:%M:%S"))
        st.dataframe(runs.drop(columns="started_at"), hide_index=True)