import calendar, io
import pandas as pd
import streamlit as st

# ================== CONFIG ==================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")
st.title("Doctor Performance — Monthwise")

CENTERS = {
    "easyhealth": "EasyHealth",
    "excellent": "Excellent",
}

REQUIRED = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

# session storage: one processed dataframe per center
if "center_data" not in st.session_state:
    st.session_state["center_data"] = {k: None for k in CENTERS.keys()}

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
    # case-insensitive exact; then heuristic for doctor/provider
    want = {c.lower().strip(): c for c in candidates}
    for c in df.columns:
        key = str(c).lower().strip()
        if key in want:
            return c
    for c in df.columns:
        k = str(c).lower().replace(" ", "")
        if any(tok in k for tok in ["docname","doc","doctor","provider","physician"]):
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def process_file(df_in) -> pd.DataFrame:
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

    # Dates → Year/Month
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
        if col not in piv.columns:
            piv[col] = 0

    # Visits per month
    visits = (
        df.groupby([n, "Year", "MonthNum", "Month"])[v]
          .nunique()
          .reset_index(name="Visits")
    )

    out = piv.merge(visits, on=[n, "Year", "MonthNum", "Month"], how="left")

    # Avg per visit = round((Consultation+Medicines+Procedure+Other)/Visits)
    row_total = out[["Consultation","Medicines","Procedure","Other"]].sum(axis=1)
    out["Avg_per_Visit"] = (
        (row_total / out["Visits"].replace(0, pd.NA))
        .round(0)
        .fillna(0)
        .astype(int)
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
mode = st.toggle("Admin mode", value=False, help="ON = upload & process; OFF = view")

# ================== CENTER PICKER (Admin & View) ==================
st.subheader("Select Center")
center_key = st.radio(
    "Center",
    list(CENTERS.keys()),
    format_func=lambda k: CENTERS[k],
    horizontal=True
)

# ================== ADMIN ==================
if mode:
    st.subheader(f"Admin — Upload & Process ({CENTERS[center_key]})")
    file = st.file_uploader(f"Upload Excel (.xlsx) for {CENTERS[center_key]}", type=["xlsx"], key=f"uploader_{center_key}")

    cols = st.columns([1, 2])
    if cols[0].button("Process", type="primary", use_container_width=True, key=f"process_{center_key}"):
        if not file:
            st.error("Please upload a file first.")
        else:
            try:
                df = load_excel(file)
                res = process_file(df)
                st.session_state["center_data"][center_key] = res
                st.success(f"✅ Processed and saved for {CENTERS[center_key]}.")
                st.dataframe(res.head(12), use_container_width=True)
                st.download_button(
                    f"Download FULL ({CENTERS[center_key]}) Doctor Performance (xlsx)",
                    data=to_excel_bytes(res),
                    file_name=f"doc_performance_all_{center_key}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error: {e}")

# ================== DOCTOR VIEWER (per center) ==================
st.subheader(f"Doctor Viewer — {CENTERS[center_key]} Monthwise Performance")

data = st.session_state["center_data"].get(center_key)
if data is None or (isinstance(data, pd.DataFrame) and data.empty):
    st.info(f"No processed data for {CENTERS[center_key]} yet. Turn ON Admin, upload and click Process.")
else:
    doctors = sorted(data["DocName"].dropna().astype(str).unique().tolist())
    selected = st.selectbox("Select Doctor", doctors, index=0, key=f"doc_select_{center_key}")

    # Filter & sort
    view = data.loc[data["DocName"] == selected, [
        "Year","Month","Consultation","Medicines","Procedure","Other","Visits","Avg_per_Visit","MonthNum"
    ]].copy()
    view = view.sort_values(["Year","MonthNum"]).reset_index(drop=True)

    # Display Year as plain string (avoid 2,024 formatting)
    view["Year"] = view["Year"].fillna(0).astype("Int64").astype(str)

    # Show table without MonthNum
    display_cols = ["Year","Month","Consultation","Medicines","Procedure","Other","Visits","Avg_per_Visit"]
    st.success(f"Doctor: **{selected}** — {CENTERS[center_key]}")
    st.dataframe(view[display_cols], use_container_width=True)

    st.download_button(
        f"Download Selected Doctor ({CENTERS[center_key]})",
        data=to_excel_bytes(view.drop(columns=["MonthNum"]), sheet=selected[:30]),
        file_name=f"doc_performance_{center_key}_{selected}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

