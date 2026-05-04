import io
import re
import hmac
import hashlib
import time
from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

# ─── SECURITY CHECK ────────────────────────────────────────────────────────────
# We only want to verify the URL parameters once per session
if "authorized" not in st.session_state:
    params = st.query_params
    t = params.get("t")
    sig = params.get("sig")
    email = params.get("email")

    if not t or not sig:
        st.error("🔒 Access Denied: Missing security token.")
        st.write("Please launch this tool directly from your purchased tools dashboard on **psjajodia.com**.")
        st.stop()
        
    try:
        # Check if URL has expired (15 minutes lifetime to allow for Streamlit app wake-up time)
        url_time = int(t)
        if time.time() * 1000 - url_time > 900000:
            st.error("⏳ Access Denied: The launch link has expired.")
            st.write("Please go back to psjajodia.com and click 'Launch Secure Tool' again.")
            st.stop()
            
        # Verify cryptographic signature
        # Check Streamlit secrets first, fallback to hardcoded if not set yet (for transition)
        secret = st.secrets.get("RAZORPAY_KEY_SECRET", "2ANGh8hrSewqCP9TDj465pht")
            
        expected_sig = hmac.new(
            secret.encode('utf-8'),
            str(t).encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_sig, sig):
            st.error("⛔ Access Denied: Invalid security signature.")
            st.write("Please ensure you are launching from psjajodia.com.")
            st.stop()
            
        # If all checks pass, mark browser as authorized for this session
        st.session_state.authorized = True
        
    except Exception as e:
        st.error(f"⛔ Access Denied: Security validation failed.")
        st.stop()
# ───────────────────────────────────────────────────────────────────────────────

OUTPUT_COLUMNS_26AS = [
    "Sr No.",
    "Part No.",
    "Name of Deductor/Collector",
    "TAN",
    "Section",
    "Date of Transaction",
    "Amount Paid/Credited",
    "TDS/TCS deposited/deducted",
]

BOOKS_COLUMNS = [
    "Sr No.",
    "Name of Deductor/Collector",
    "TAN",
    "Section",
    "Date of Transaction",
    "Amount Paid/Credited",
    "TDS/TCS deposited/deducted",
]


def normalize_tan(v):
    return str(v or "").strip().upper()


def normalize_section(v):
    return str(v or "").strip().upper()


def to_float(v):
    if pd.isna(v):
        return None
    s = str(v).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(v):
    if pd.isna(v):
        return pd.NaT
    s = str(v).strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return pd.to_datetime(datetime.strptime(s, fmt).date())
        except ValueError:
            continue
    return pd.to_datetime(s, errors="coerce")


def parse_26as_text(raw_text: str) -> pd.DataFrame:
    rows = []
    current_part = None
    current_name = None
    current_tan = None

    header_pat = re.compile(r"^\d+\^[^\^]+\^[A-Z0-9]{10}\^")
    tx_pat = re.compile(r"^\^\d+\^[^\^]+\^[0-9]{2}-[A-Za-z]{3}-[0-9]{4}\^")

    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue

        if "PART-I - Details of Tax Deducted at Source" in line:
            current_part = "PART-I"
            continue
        if "PART-VI - Details of Tax Collected at Source" in line:
            current_part = "PART-VI"
            continue
        if line.startswith("^PART-") and "PART-I" not in line and "PART-VI" not in line:
            current_part = None
            continue

        if current_part in {"PART-I", "PART-VI"} and header_pat.match(line):
            parts = line.split("^")
            current_name = parts[1].strip()
            current_tan = parts[2].strip().upper()
            continue

        if current_part in {"PART-I", "PART-VI"} and tx_pat.match(line):
            parts = line.split("^")
            if len(parts) < 10:
                continue
            tx_sr = parts[1].strip()
            section = parts[2].strip()
            tx_date = parts[3].strip()
            amount = parts[7].strip()
            tax = parts[9].strip()

            rows.append(
                {
                    "Sr No.": tx_sr,
                    "Part No.": current_part,
                    "Name of Deductor/Collector": current_name,
                    "TAN": current_tan,
                    "Section": section,
                    "Date of Transaction": tx_date,
                    "Amount Paid/Credited": to_float(amount),
                    "TDS/TCS deposited/deducted": to_float(tax),
                }
            )

    df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS_26AS)
    if not df.empty:
        df["Date of Transaction"] = df["Date of Transaction"].apply(parse_date)
        df = df.sort_values(["Date of Transaction", "TAN", "Section"], na_position="last").reset_index(drop=True)
        df["Sr No."] = range(1, len(df) + 1)
    return df


def tan_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["TAN", "Name of Deductor/Collector", "Total Amount", "Total TDS/TCS"])
    s = (
        df.groupby(["TAN", "Name of Deductor/Collector"], dropna=False, as_index=False)
        .agg({"Amount Paid/Credited": "sum", "TDS/TCS deposited/deducted": "sum"})
        .rename(columns={"Amount Paid/Credited": "Total Amount", "TDS/TCS deposited/deducted": "Total TDS/TCS"})
    )
    return s


def reconcile(df26: pd.DataFrame, dfb: pd.DataFrame) -> pd.DataFrame:
    a = df26.copy()
    b = dfb.copy()
    for d in (a, b):
        d["TAN"] = d["TAN"].apply(normalize_tan)
        d["Section"] = d["Section"].apply(normalize_section)
        d["Date of Transaction"] = d["Date of Transaction"].apply(parse_date)
        d["Amount Paid/Credited"] = d["Amount Paid/Credited"].apply(to_float)
        d["TDS/TCS deposited/deducted"] = d["TDS/TCS deposited/deducted"].apply(to_float)

    a = a.sort_values(["Date of Transaction", "TAN", "Section"]).reset_index(drop=True)
    b = b.sort_values(["Date of Transaction", "TAN", "Section"]).reset_index(drop=True)
    used_a, used_b = set(), set()
    out = []

    def add_match(i, j, status):
        used_a.add(i)
        used_b.add(j)
        ra, rb = a.loc[i], b.loc[j]
        out.append({
            "Status": status,
            "TAN": ra["TAN"] or rb["TAN"],
            "Section": ra["Section"] or rb["Section"],
            "Date 26AS": ra["Date of Transaction"],
            "Date Books": rb["Date of Transaction"],
            "Amount 26AS": ra["Amount Paid/Credited"],
            "Amount Books": rb["Amount Paid/Credited"],
            "Amount Diff": (ra["Amount Paid/Credited"] or 0) - (rb["Amount Paid/Credited"] or 0),
            "Tax 26AS": ra["TDS/TCS deposited/deducted"],
            "Tax Books": rb["TDS/TCS deposited/deducted"],
            "Tax Diff": (ra["TDS/TCS deposited/deducted"] or 0) - (rb["TDS/TCS deposited/deducted"] or 0),
            "Name 26AS": ra["Name of Deductor/Collector"],
            "Name Books": rb["Name of Deductor/Collector"],
        })

    b_exact = defaultdict(list)
    for j, r in b.iterrows():
        key = (r["TAN"], r["Section"], r["Date of Transaction"], r["Amount Paid/Credited"], r["TDS/TCS deposited/deducted"])
        b_exact[key].append(j)
    for i, r in a.iterrows():
        key = (r["TAN"], r["Section"], r["Date of Transaction"], r["Amount Paid/Credited"], r["TDS/TCS deposited/deducted"])
        while b_exact[key] and b_exact[key][0] in used_b:
            b_exact[key].pop(0)
        if b_exact[key]:
            add_match(i, b_exact[key].pop(0), "Exact Match")

    grp_a, grp_b = defaultdict(list), defaultdict(list)
    for i, r in a.iterrows():
        if i not in used_a:
            grp_a[(r["TAN"], r["Section"], r["Date of Transaction"])].append(i)
    for j, r in b.iterrows():
        if j not in used_b:
            grp_b[(r["TAN"], r["Section"], r["Date of Transaction"])].append(j)
    for key in set(grp_a) & set(grp_b):
        ai = sorted(grp_a[key], key=lambda x: ((a.loc[x, "Amount Paid/Credited"] or 0), (a.loc[x, "TDS/TCS deposited/deducted"] or 0)))
        bj = sorted(grp_b[key], key=lambda x: ((b.loc[x, "Amount Paid/Credited"] or 0), (b.loc[x, "TDS/TCS deposited/deducted"] or 0)))
        for i, j in zip(ai, bj):
            add_match(i, j, "Matched but Amount Difference")

    grp_a2, grp_b2 = defaultdict(list), defaultdict(list)
    for i, r in a.iterrows():
        if i not in used_a:
            grp_a2[(r["TAN"], r["Section"], r["Amount Paid/Credited"], r["TDS/TCS deposited/deducted"])].append(i)
    for j, r in b.iterrows():
        if j not in used_b:
            grp_b2[(r["TAN"], r["Section"], r["Amount Paid/Credited"], r["TDS/TCS deposited/deducted"])].append(j)
    for key in set(grp_a2) & set(grp_b2):
        for i, j in zip(sorted(grp_a2[key]), sorted(grp_b2[key])):
            add_match(i, j, "Matched but Date Difference")

    for i, r in a.iterrows():
        if i not in used_a:
            out.append({"Status": "Only in 26AS", "TAN": r["TAN"], "Section": r["Section"], "Date 26AS": r["Date of Transaction"], "Date Books": pd.NaT, "Amount 26AS": r["Amount Paid/Credited"], "Amount Books": None, "Amount Diff": r["Amount Paid/Credited"], "Tax 26AS": r["TDS/TCS deposited/deducted"], "Tax Books": None, "Tax Diff": r["TDS/TCS deposited/deducted"], "Name 26AS": r["Name of Deductor/Collector"], "Name Books": None})
    for j, r in b.iterrows():
        if j not in used_b:
            out.append({"Status": "Only in Books", "TAN": r["TAN"], "Section": r["Section"], "Date 26AS": pd.NaT, "Date Books": r["Date of Transaction"], "Amount 26AS": None, "Amount Books": r["Amount Paid/Credited"], "Amount Diff": -1 * (r["Amount Paid/Credited"] or 0), "Tax 26AS": None, "Tax Books": r["TDS/TCS deposited/deducted"], "Tax Diff": -1 * (r["TDS/TCS deposited/deducted"] or 0), "Name 26AS": None, "Name Books": r["Name of Deductor/Collector"]})

    return pd.DataFrame(out).sort_values(["TAN", "Section", "Date 26AS", "Date Books"], na_position="last")


def to_excel_bytes(dataframes: dict[str, pd.DataFrame]) -> bytes:
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter", datetime_format="dd-mmm-yyyy") as writer:
        for sheet, df in dataframes.items():
            df.to_excel(writer, index=False, sheet_name=sheet[:31])
    return bio.getvalue()


# ─── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TDS Reconciliation Tool | P S Jajodia & Associates",
    page_icon="🧮",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── Branding Header ───────────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }
    .brand-header { 
        background: linear-gradient(135deg, #0B1C2D 0%, #1E3A5F 100%);
        color: white; padding: 1.2rem 2rem; border-radius: 12px;
        margin-bottom: 1.5rem; display: flex; align-items: center; gap: 1rem;
    }
    .brand-title { font-size: 1.4rem; font-weight: 700; margin: 0; }
    .brand-sub { font-size: 0.8rem; color: #C6A75E; margin: 0; letter-spacing: 0.05em; }
    .stDownloadButton button { background: #C6A75E !important; color: white !important; border: none !important; }
    .stDownloadButton button:hover { background: #A88B3D !important; }
</style>
<div class="brand-header">
    <div style="font-size:2.5rem;">🧮</div>
    <div>
        <p class="brand-title">TDS / TCS Reconciliation Tool</p>
        <p class="brand-sub">P S JAJODIA & ASSOCIATES — CHARTERED ACCOUNTANTS, NAGPUR</p>
    </div>
</div>
""", unsafe_allow_html=True)

st.title("TDS/TCS Reconciliation Tool")

st.header("A. Convert Raw 26AS TXT to Structured Excel")
raw_file = st.file_uploader("Upload raw 26AS .txt", type=["txt"], key="raw")

if raw_file is not None:
    text = raw_file.read().decode("utf-8", errors="ignore")
    df26 = parse_26as_text(text)
    summary26 = tan_summary(df26)
    st.write(f"Parsed rows: {len(df26)}")
    st.dataframe(df26.head(30), use_container_width=True)
    x26 = to_excel_bytes({"Structured_26AS": df26, "TAN wise total summary": summary26})
    st.download_button("Download Structured 26AS Excel", data=x26, file_name="structured_26as.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.header("B. Books Ledger Template")
template_df = pd.DataFrame(columns=BOOKS_COLUMNS)
st.download_button("Download Books Ledger Template", data=to_excel_bytes({"Books_Ledger_Template": template_df}), file_name="books_ledger_template.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.header("C & D. Reconciliation")
col1, col2 = st.columns(2)
with col1:
    structured_26as_file = st.file_uploader("Upload Structured 26AS Excel", type=["xlsx"], key="s26")
with col2:
    books_file = st.file_uploader("Upload Books Ledger Excel", type=["xlsx"], key="books")

if structured_26as_file is not None and books_file is not None:
    df26_in = pd.read_excel(structured_26as_file)
    dfb_in = pd.read_excel(books_file)

    for col in OUTPUT_COLUMNS_26AS:
        if col not in df26_in.columns:
            st.error(f"Structured 26AS missing column: {col}")
            st.stop()
    for col in BOOKS_COLUMNS:
        if col not in dfb_in.columns:
            st.error(f"Books ledger missing column: {col}")
            st.stop()

    rec = reconcile(df26_in[OUTPUT_COLUMNS_26AS], dfb_in[BOOKS_COLUMNS])
    status_counts = rec["Status"].value_counts().rename_axis("Status").reset_index(name="Count")

    st.subheader("Status Buckets")
    st.dataframe(status_counts, use_container_width=True)

    st.subheader("Reconciled Detailed Transactions")
    st.dataframe(rec, use_container_width=True)

    rec_summary = rec.groupby(["TAN", "Status"], as_index=False).agg({"Amount Diff": "sum", "Tax Diff": "sum"})
    st.subheader("Reconciled TAN-wise Summary")
    st.dataframe(rec_summary, use_container_width=True)

    out_xlsx = to_excel_bytes({"Reconciled_Detail": rec, "Reconciled_TAN_Summary": rec_summary})
    st.download_button("Download Reconciled Excel", data=out_xlsx, file_name="tds_reconciliation_output.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.markdown("---")
st.markdown(
    "<p style='text-align:center;color:#8A8AA0;font-size:0.75rem;'>"
    "© 2025 P S Jajodia & Associates | Chartered Accountants, Nagpur | "
    "<a href='https://psjajodia.com' style='color:#C6A75E;'>psjajodia.com</a>"
    "</p>",
    unsafe_allow_html=True,
)
