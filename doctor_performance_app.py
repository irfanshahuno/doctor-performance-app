import calendar, io
from pathlib import Path
import pandas as pd
import streamlit as st

# ================== CONFIG ==================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")
st.title("Doctor Performance — Monthwise")

CENTERS = {"easyhealth": "EasyHealth", "excellent": "Excellent"}
REQUIRED = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

BASE = Path(__file__).parent
STORE = BASE / "processed"
STORE.mkdir(parents=True, exist_ok=True)

# ================== SESSION INIT + DISK LOAD ==================
def load_center_from_disk(center_key: str) -> pd.DataFrame | None:
    f = STORE / f"{center_key}.csv"
    if f.exists():
        try:
            df = pd.read_csv(f)
            return df
        except Exception:
            return None
    return None

def save_center_to_disk(center_key: str, df: pd.DataFrame):
    f = STORE / f"{center_key}.csv"
    df.to_csv(f, index=False)

if "center_data" not in st.session_state:
    # try to hydrate from disk so View mode works after reload
    st.session_state["center_data"] = {k: load_center_from_disk(k) for k in CENTERS.keys()}

# ================== HELPERS ==================
def safe_month_label(n):
    try:
        n = int(float(n))
        return calendar.month_abbr[n] if 1 <= n <= 12 else ""
    except Exception:
        return ""

def normalize_cols(df):
    df.columns = [" ".join(str(c).strip().split()) for c in df.columns]
    return df

def find_col(df, *candidates):
    want = {c.lower().strip(): c for c in candidates}
    for c in df.columns:
        key = str(c).lower().strip()
        if key in want:
            return c
    # heuristic for doctor/provider-like names
    for c in df.columns:
        k = str(c).lower().replace(" ", "")
        if any(tok in k for tok in ["docname", "doc", "doctor", "provider", "physician"]):
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def process_file(df_in) -> pd.DataFrame:
    """
    Month-wise doctor summary with Total & Avg_per_Visit.
    - Sum money on ALL lines (no global drop_duplicates).
    - Visits = distinct VisitNo per (Doc, Year, Month).
    """
    df = normalize_cols(df_in.copy())

    v = find_col(df, "VisitNo", "Visit No", "Visit_ID", "Visit Id")
    d = find_col(df, "VisitDate", "Visit Date", "Date")
    n = find_col(df, "DocName", "Doc Name", "Doctor", "Doctor Name", "Provider", "Provider Name")
    g = find_col(df, "Item Group", "ItemGroup", "Group")
    # Prefer Net Amount if present; otherwise ActivityIns/Amount
    a = find_col(df, "Net Amount", "NetAmount", "ActivityIns", "Activity Ins", "Amount")

    missing = [label for label, col in [
        ("VisitNo", v), ("VisitDate", d), ("DocName", n), ("Item Group", g), ("Amount (Net/ActivityIns)", a)
    ] if col is None]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    # Dates -> Year/Month
    dt = pd.to_datetime(df[d], errors="coerce")
    df["Year"] = dt.dt.year
    df["MonthNum"] = dt.dt.month.astype("Int64")
    df["Month"] = df["MonthNum"].apply(safe_month_label)

    bad_dates = int(df["MonthNum"].isna().sum())
    if bad_dates > 0:
        st.warning(f"{bad_dates} row(s) had invalid VisitDate and were excluded from month buckets.")

    # Amount and bucket
    df[a] = pd.to_numeric(df[a], errors="coerce").fillna(0)
    ig = df[g].astype(str).str.strip().str.title()
    bucket_map = {
        "Consultation": "Consultation", "Consultations": "Consultation",
        "Medicine": "Medicines", "Medicines": "Medicines", "Drug": "Medicines", "Drugs": "Medicines",
        "Procedure": "Procedure", "Procedures": "Procedure",
    }
    df["Bucket"] = ig.map(bucket_map).fillna("Other")

    # AMOUNTS: sum all lines (exclude only bad-date rows)
    amounts = (
        df[df["MonthNum"].notna()]
        .pivot_table(
            index=[n, "Year", "MonthNum", "Month"],
            columns="Bucket",
            values=a,
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    for col in ["Consultation", "Medicines", "Procedure", "Other"]:
        if col not in amounts.columns:
            amounts[col] = 0

    # VISITS: distinct VisitNo per group
    df_visits = df.loc[df["MonthNum"].notna(), [n, "Year", "MonthNum", "Month", v]].copy()
    df_visits[v] = df_visits[v].astype(str).str.strip()
    visits = (
        df_visits.groupby([n, "Year", "MonthNum", "Month"])[v]
                 .nunique()
                 .reset_index(name="Visits")
    )

    out = amounts.merge(visits, on=[n, "Year", "MonthNum", "Month"], how="left")
    out["Visits"] = out["Visits"].fillna(0).astype(int)
    out["Total"] = out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1)
    out["Avg_per_Visit"] = (
        (out["Total"] / out["Visits"].replace(0, pd.NA))
        .round(0)
        .fillna(0)
        .astype(int)
    )

    out = out.sort_values([n, "Year", "MonthNum"]).reset_index(drop=True)
    out = out.rename(columns={n: "DocName"})  # standardize for viewer
    return out

def to_excel_bytes(df, sheet="Doctor_Summary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet)
    return buf.getvalue()

def render_center_view(center_key: str):
    """Doctor dropdown + month table + downloads for the selected center."""
    data = st.session_state["center_data"].get(center_key)
    if data is None or (isinstance(data, pd.DataFrame) and data.empty):
        st.info(f"No processed data for {CENTERS[center_key]} yet. Turn ON Admin, upload and click Process.")
        return

    doctors = sorted(pd.Series(data["DocName"]).dropna().astype(str).unique().tolist())
    selected = st.selectbox("Select Doctor", doctors, index=0, key=f"doc_select_{center_key}")

    # Filter & sort
    view = data.loc[data["DocName"] == selected, [
        "Year","Month","Consultation","Medicines","Procedure","Other","Total","Visits","Avg_per_Visit","MonthNum"
    ]].copy()
    view = view.sort_values(["Year","MonthNum"]).reset_index(drop=True)

    # Display Year as plain string (avoid 2,024)
    view["Year"] = view["Year"].fillna(0).astype("Int64").astype(str)

    st.success(f"Doctor: **{selected}** — {CENTERS[center_key]}")
    display_cols = ["Year","Month","Consultation","Medicines","Procedure","Other","Total","Visits","Avg_per_Visit"]
    st.dataframe(view[display_cols], use_container_width=True)

    st.download_button(
        f"Download Selected Doctor ({CENTERS[center_key]})",
        data=to_excel_bytes(view.drop(columns=["MonthNum"]), sheet=selected[:30]),
        file_name=f"doc_performance_{center_key}_{selected}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# ================== UI ==================
mode = st.toggle("Admin mode", value=False, help="ON = upload & process; OFF = view")

st.subheader("Select Center")
center_key = st.radio("Center", list(CENTERS.keys()), format_func=lambda k: CENTERS[k], horizontal=True)

if mode:
    st.subheader(f"Admin — Upload & Process ({CENTERS[center_key]})")
    file = st.file_uploader(
        f"Upload Excel (.xlsx) for {CENTERS[center_key]}",
        type=["xlsx"],
        key=f"uploader_{center_key}"
    )
    c1, c2, c3 = st.columns([1,1,2])
    if c1.button("Process", type="primary", use_container_width=True, key=f"process_{center_key}"):
        if not file:
            st.error("Please upload a file first.")
        else:
            try:
                df = load_excel(file)
                res = process_file(df)
                st.session_state["center_data"][center_key] = res
                save_center_to_disk(center_key, res)  # <-- persist to disk so View works after reload
                st.success(f"✅ Processed and saved for {CENTERS[center_key]}.")
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


