import calendar, io
from pathlib import Path
import pandas as pd
import streamlit as st

# ================== PAGE CONFIG ==================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")
st.title("Doctor Performance — Monthwise")

# Centers
CENTERS = {"easyhealth": "EasyHealth", "excellent": "Excellent"}

# Storage folder to persist processed data (so View works after reload)
BASE = Path(__file__).parent
STORE = BASE / "processed"
STORE.mkdir(parents=True, exist_ok=True)

# ================== SESSION INIT (hydrate from disk) ==================
def load_center_from_disk(center_key: str) -> pd.DataFrame | None:
    f = STORE / f"{center_key}.csv"
    if f.exists():
        try:
            return pd.read_csv(f)
        except Exception:
            return None
    return None

def save_center_to_disk(center_key: str, df: pd.DataFrame):
    (STORE / f"{center_key}.csv").write_text(df.to_csv(index=False))

if "center_data" not in st.session_state:
    st.session_state["center_data"] = {k: load_center_from_disk(k) for k in CENTERS.keys()}

# ================== HELPERS ==================
def safe_month_label(n):
    try:
        n = int(float(n))
        return calendar.month_abbr[n] if 1 <= n <= 12 else ""
    except Exception:
        return ""

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [" ".join(str(c).strip().split()) for c in df.columns]
    return df

def find_col(df, *candidates):
    """Case-insensitive exact; then heuristic for doctor/provider-like columns."""
    wanted = {c.lower().strip(): c for c in candidates}
    for c in df.columns:
        if str(c).lower().strip() in wanted:
            return c
    for c in df.columns:
        k = str(c).lower().replace(" ", "")
        if any(tok in k for tok in ["docname", "doc", "doctor", "provider", "physician"]):
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

# ---------- Keyword mapping for Item Group → Bucket ----------
CONSULTATION_KEYS = [
    "consult", "opd", "follow up", "follow-up", "revisit", "tele", "teleconsult"
]
MEDICINE_KEYS = [
    "medicine", "medicin", "drug", "pharmacy", "rx", "tablet", "capsule",
    "syrup", "drops", "cream", "ointment", "spray", "suppository"
]
PROCEDURE_KEYS = [
    "procedure", "proc", "injection", "inj", "iv", "infusion", "nebul",
    "ecg", "dressing", "suturing", "removal", "x-ray", "xray", "ultrasound",
    "usg", "physio", "cast", "lab sample", "venipuncture", "nebulization",
    "ivf", "drip", "echo", "spirometry", "vaccin", "vaccine", "bandage"
]

def categorize_group(x: str) -> str:
    if pd.isna(x):
        return "Other"
    s = str(x).strip().lower()
    if any(k in s for k in CONSULTATION_KEYS):
        return "Consultation"
    if any(k in s for k in MEDICINE_KEYS):
        return "Medicines"
    if any(k in s for k in PROCEDURE_KEYS):
        return "Procedure"
    return "Other"

# ================== CORE PROCESSOR ==================
def process_file(df_in: pd.DataFrame) -> pd.DataFrame:
    """
    Month-wise doctor summary with Total & Avg_per_Visit.
    * Sum money on ALL lines (no global drop_duplicates).
    * Visits = distinct VisitNo per (Doc, Year, Month).
    * Robust Item Group → Bucket mapping.
    """
    df = normalize_cols(df_in)

    visit_col = find_col(df, "VisitNo", "Visit No", "Visit_ID", "Visit Id")
    date_col  = find_col(df, "VisitDate", "Visit Date", "Date")
    doc_col   = find_col(df, "DocName", "Doc Name", "Doctor", "Doctor Name", "Provider", "Provider Name")
    group_col = find_col(df, "Item Group", "ItemGroup", "Group")
    # prefer Net Amount if available; fallback to ActivityIns/Amount
    amt_col   = find_col(df, "Net Amount", "NetAmount", "ActivityIns", "Activity Ins", "Amount")

    missing = [label for label, col in [
        ("VisitNo", visit_col), ("VisitDate", date_col), ("DocName", doc_col),
        ("Item Group", group_col), ("Amount (Net/ActivityIns)", amt_col)
    ] if col is None]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    # Dates → Year/Month
    dt = pd.to_datetime(df[date_col], errors="coerce")
    df["Year"] = dt.dt.year
    df["MonthNum"] = dt.dt.month.astype("Int64")
    df["Month"] = df["MonthNum"].apply(safe_month_label)
    bad_dates = int(df["MonthNum"].isna().sum())
    if bad_dates > 0:
        st.warning(f"{bad_dates} row(s) had invalid VisitDate and were excluded from month buckets.")

    # Amounts and Buckets
    df[amt_col] = pd.to_numeric(df[amt_col], errors="coerce").fillna(0)
    df["Bucket"] = df[group_col].apply(categorize_group)

    # --- AMOUNTS: sum ALL lines by Doc × Year × Month × Bucket ---
    ok = df["MonthNum"].notna()
    amounts = (
        df.loc[ok]
          .pivot_table(
              index=[doc_col, "Year", "MonthNum", "Month"],
              columns="Bucket",
              values=amt_col,
              aggfunc="sum",
              fill_value=0,
          )
          .reset_index()
    )
    for col in ["Consultation", "Medicines", "Procedure", "Other"]:
        if col not in amounts.columns:
            amounts[col] = 0

    # --- VISITS: distinct VisitNo per group ---
    vdf = df.loc[ok, [doc_col, "Year", "MonthNum", "Month", visit_col]].copy()
    vdf[visit_col] = vdf[visit_col].astype(str).str.strip()
    visits = (
        vdf.groupby([doc_col, "Year", "MonthNum", "Month"])[visit_col]
           .nunique()
           .reset_index(name="Visits")
    )

    # Merge + totals
    out = amounts.merge(visits, on=[doc_col, "Year", "MonthNum", "Month"], how="left")
    out["Visits"] = out["Visits"].fillna(0).astype(int)
    out["Total"] = out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1)
    out["Avg_per_Visit"] = (
        (out["Total"] / out["Visits"].replace(0, pd.NA))
        .round(0)
        .fillna(0)
        .astype(int)
    )

    out = out.sort_values([doc_col, "Year", "MonthNum"]).reset_index(drop=True)
    out = out.rename(columns={doc_col: "DocName"})  # standardize for viewer
    out.attrs["amt_col_used"] = amt_col  # keep a hint for the UI
    out.attrs["group_col_used"] = group_col
    return out

def to_excel_bytes(df, sheet="Doctor_Summary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet)
    return buf.getvalue()

def render_bucket_debug(raw_df: pd.DataFrame, processed_df: pd.DataFrame, group_col: str, amt_col: str):
    """Show which Item Groups are still mapped as OTHER (top 50 by amount)."""
    with st.expander("Bucket debug — groups currently mapped as OTHER"):
        df = normalize_cols(raw_df)
        gc = group_col
        ac = amt_col
        if gc not in df.columns or ac not in df.columns:
            st.info("Source columns not available for debugging.")
            return
        tmp = df.copy()
        tmp["Bucket"] = tmp[gc].apply(categorize_group)
        tmp[ac] = pd.to_numeric(tmp[ac], errors="coerce").fillna(0)
        dbg = (
            tmp.loc[tmp["Bucket"]=="Other", [gc, ac]]
               .groupby(gc, dropna=False)[ac]
               .agg(lines="count", amount="sum")
               .sort_values("amount", ascending=False)
               .head(50)
        )
        st.write(dbg)

# ================== UI HELPERS ==================
def render_center_view(center_key: str):
    """Doctor dropdown + month table + download for the selected center."""
    data = st.session_state["center_data"].get(center_key)
    if data is None or (isinstance(data, pd.DataFrame) and data.empty):
        st.info(f"No processed data for {CENTERS[center_key]} yet. Turn ON Admin, upload and click Process.")
        return

    doctors = sorted(pd.Series(data["DocName"]).dropna().astype(str).unique().tolist())
    selected = st.selectbox("Select Doctor", doctors, index=0, key=f"doc_select_{center_key}")

    view = data.loc[data["DocName"] == selected, [
        "Year","Month","Consultation","Medicines","Procedure","Other","Total","Visits","Avg_per_Visit","MonthNum"
    ]].copy()
    view = view.sort_values(["Year","MonthNum"]).reset_index(drop=True)

    # display 2024 not 2,024
    view["Year"] = view["Year"].fillna(0).astype("Int64").astype(str)

    st.success(f"Doctor: **{selected}** — {CENTERS[center_key]}")
    st.dataframe(
        view[["Year","Month","Consultation","Medicines","Procedure","Other","Total","Visits","Avg_per_Visit"]],
        use_container_width=True
    )

    st.download_button(
        f"Download Selected Doctor ({CENTERS[center_key]})",
        data=to_excel_bytes(view.drop(columns=["MonthNum"]), sheet=selected[:30]),
        file_name=f"doc_performance_{center_key}_{selected}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ================== LAYOUT ==================
mode = st.toggle("Admin mode", value=False, help="ON = upload & process; OFF = view")

st.subheader("Select Center")
center_key = st.radio("Center", list(CENTERS.keys()), format_func=lambda k: CENTERS[k], horizontal=True)

if mode:
    st.subheader(f"Admin — Upload & Process ({CENTERS[center_key]})")
    up = st.file_uploader(
        f"Upload Excel (.xlsx) for {CENTERS[center_key]}",
        type=["xlsx"],
        key=f"uploader_{center_key}"
    )
    c1, c2, c3 = st.columns([1,1,2])
    if c1.button("Process", type="primary", use_container_width=True, key=f"process_{center_key}"):
        if not up:
            st.error("Please upload a file first.")
        else:
            try:
                source_df = load_excel(up)
                result_df = process_file(source_df)
                st.session_state["center_data"][center_key] = result_df
                save_center_to_disk(center_key, result_df)
                st.success(f"✅ Processed and saved for {CENTERS[center_key]}.")
                # bucket debug
                amt_col_used = result_df.attrs.get("amt_col_used", "Amount")
                group_col_used = result_df.attrs.get("group_col_used", "Item Group")
                render_bucket_debug(source_df, result_df, group_col_used, amt_col_used)
            except Exception as e:
                st.error(f"Error: {e}")

    if c2.button("Clear saved data", use_container_width=True, key=f"clear_{center_key}"):
        st.session_state["center_data"][center_key] = None
        f = STORE / f"{center_key}.csv"
        if f.exists():
            f.unlink()
        st.info(f"Cleared stored data for {CENTERS[center_key]}.")

    st.subheader(f"Doctor Viewer — {CENTERS[center_key]}")
    render_center_view(center_key)

else:
    st.subheader(f"Doctor Viewer — {CENTERS[center_key]}")
    render_center_view(center_key)




