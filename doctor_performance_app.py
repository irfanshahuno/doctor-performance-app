import calendar, io
import pandas as pd
import streamlit as st

# ================== CONFIG ==================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")
st.title("Doctor Performance — Monthwise")
REQUIRED = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

if "processed_df" not in st.session_state:
    st.session_state["processed_df"] = None

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
    # heuristic fallback
    for c in df.columns:
        k = str(c).lower().replace(" ", "")
        if any(tok in k for tok in ["docname","doc","doctor","provider","physician"]):
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def process_file(df_in):
    df = normalize_cols(df_in.copy())

    v = find_col(df, "VisitNo", "Visit No", "Visit_ID", "Visit Id")
    d = find_col(df, "VisitDate", "Visit Date", "Date")
    n = find_col(df, "DocName", "Doc Name", "Doctor", "Doctor Name", "Provider", "Provider Name")
    g = find_col(df, "Item Group", "ItemGroup", "Group")
    a = find_col(df, "ActivityIns", "Activity Ins", "Amount", "NetAmount", "Net Amount")

    missing = [label for label, col in [
        ("VisitNo", v), ("VisitDate", d), ("DocName", n), ("Item Group", g), ("ActivityIns", a)
    ] if col is None]
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    # Unique visits
    df[v] = df[v].astype(str).str.strip()
    df = df.drop_duplicates(subset=[v])

    # Dates
    dt = pd.to_datetime(df[d], errors="coerce")
    df["Year"] = dt.dt.year
    df["MonthNum"] = dt.dt.month.astype("Int64")
    df["Month"] = df["MonthNum"].apply(safe_month_label)

    # Amount
    df[a] = pd.to_numeric(df[a], errors="coerce").fillna(0)

    # Buckets
    ig = df[g].astype(str).str.strip().str.title()
    bucket_map = {
        "Consultation": "Consultation", "Consultations": "Consultation",
        "Medicine": "Medicines", "Medicines": "Medicines", "Drug": "Medicines", "Drugs": "Medicines",
        "Procedure": "Procedure", "Procedures": "Procedure",
    }
    df["Bucket"] = ig.map(bucket_map).fillna("Other")

    # Aggregate amounts
    piv = (
        df.pivot_table(
            index=[n, "Year", "MonthNum", "Month"],
            columns="Bucket",
            values=a,
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    for col in ["Consultation", "Medicines", "Procedure", "Other"]:
        if col not in piv.columns: piv[col] = 0

    # Visits per month
    visits = df.groupby([n, "Year", "MonthNum", "Month"])[v].nunique().reset_index(name="Visits")

    out = piv.merge(visits, on=[n, "Year", "MonthNum", "Month"], how="left")

    # Avg per visit (NO Row_Total kept)
    out["Avg_per_Visit"] = (
        (out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1) / out["Visits"].replace(0, pd.NA))
        .round(0).fillna(0).astype(int)
    )

    out = out.sort_values([n, "Year", "MonthNum"]).reset_index(drop=True)
    out = out.rename(columns={n: "DocName"})  # standardize
    return out

def to_excel_bytes(df, sheet="Doctor_Summary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet)
    return buf.getvalue()

# ================== MODE TOGGLE ==================
mode = st.toggle("Admin mode", value=False, help="ON = upload & process; OFF = view only")

# ================== ADMIN ==================
if mode:
    st.subheader("Admin — Upload & Process")
    file = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    cols = st.columns([1, 2])
    if cols[0].button("Process", type="primary", use_container_width=True):
        if not file:
            st.error("Please upload a file first.")
        else:
            try:
                df = load_excel(file)
                res = process_file(df)
                st.session_state["processed_df"] = res
                st.success("✅ Processed and saved.")
                # quick preview of all docs (admin-only)
                st.dataframe(res.head(12), use_container_width=True)
                st.download_button(
                    "Download FULL Doctor Performance (xlsx)",
                    data=to_excel_bytes(res),
                    file_name="doc_performance_all.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error: {e}")

# ================== DOCTOR VIEWER (always shown if data exists) ==================
st.subheader("Doctor Viewer — Monthwise Performance")

data = st.session_state.get("processed_df")
if data is None or data.empty:
    st.info("No processed data yet. Turn ON Admin, upload and click Process.")
else:
    # Doctor dropdown ALWAYS visible once data exists
    doctors = sorted(data["DocName"].dropna().astype(str).unique().tolist())
    selected = st.selectbox("Select Doctor", doctors, index=0)

    view = (
        data.loc[data["DocName"] == selected, ["Year", "Month", "Consultation", "Medicines", "Procedure", "Other", "Visits", "Avg_per_Visit"]]
            .sort_values(["Year", "Month"], key=lambda s: s.map({m:i for i,m in enumerate(["",*calendar.month_abbr[1:]], start=0)}))
            .reset_index(drop=True)
    )

    st.success(f"Doctor: **{selected}**")
    st.dataframe(view, use_container_width=True)

    st.download_button(
        "Download Selected Doctor (xlsx)",
        data=to_excel_bytes(view, sheet=selected[:30]),
        file_name=f"doc_performance_{selected}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

