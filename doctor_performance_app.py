import calendar
import io
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

def normalize(df):
    df.columns = [str(c).strip() for c in df.columns]
    return df

def find_col(df, target):
    for c in df.columns:
        if c.lower().strip() == target.lower():
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def process_file(df):
    df = normalize(df)
    v = find_col(df, "VisitNo")
    d = find_col(df, "VisitDate")
    n = find_col(df, "DocName")
    g = find_col(df, "Item Group")
    a = find_col(df, "ActivityIns")
    if None in [v, d, n, g, a]:
        raise ValueError("Missing one or more required columns.")

    df[v] = df[v].astype(str).str.strip()
    df = df.drop_duplicates(subset=[v])
    dt = pd.to_datetime(df[d], errors="coerce")
    df["Year"] = dt.dt.year
    df["MonthNum"] = dt.dt.month.astype("Int64")
    df["Month"] = df["MonthNum"].apply(safe_month_label)
    df[a] = pd.to_numeric(df[a], errors="coerce").fillna(0)

    ig = df[g].astype(str).str.title()
    bucket_map = {
        "Consultation": "Consultation",
        "Consultations": "Consultation",
        "Medicine": "Medicines",
        "Medicines": "Medicines",
        "Drug": "Medicines",
        "Drugs": "Medicines",
        "Procedure": "Procedure",
        "Procedures": "Procedure",
    }
    df["Bucket"] = ig.map(bucket_map).fillna("Other")

    piv = df.pivot_table(
        index=[n, "Year", "MonthNum", "Month"],
        columns="Bucket",
        values=a,
        aggfunc="sum",
        fill_value=0,
    ).reset_index()

    for col in ["Consultation", "Medicines", "Procedure", "Other"]:
        if col not in piv.columns:
            piv[col] = 0

    visits = df.groupby([n, "Year", "MonthNum", "Month"])[v].nunique().reset_index(name="Visits")
    out = piv.merge(visits, on=[n, "Year", "MonthNum", "Month"], how="left")
    out["Avg_per_Visit"] = (
        (out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1) / out["Visits"])
        .round(0)
        .fillna(0)
        .astype(int)
    )
    out = out.sort_values([n, "Year", "MonthNum"]).reset_index(drop=True)
    return out

def to_excel_bytes(df, name="Doctor_Summary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=name)
    return buf.getvalue()

# ================== MODE ==================
mode = st.toggle("Admin mode", value=False)

# ================== ADMIN ==================
if mode:
    st.subheader("Admin — Upload & Process File")
    file = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    colA, colB = st.columns([1, 2])
    if colA.button("Process", type="primary"):
        if not file:
            st.error("Please upload a file first.")
        else:
            try:
                df = load_excel(file)
                result = process_file(df)
                st.session_state["processed_df"] = result
                st.success("✅ Processed successfully.")
                st.dataframe(result.head(10), use_container_width=True)
                xbytes = to_excel_bytes(result)
                st.download_button(
                    "Download Full Doctor Performance (xlsx)",
                    data=xbytes,
                    file_name="doc_performance_all.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as e:
                st.error(f"Error: {e}")

# ================== VIEW ==================
else:
    st.subheader("View — Select Doctor Performance")
    df = st.session_state.get("processed_df")

    if df is None or df.empty:
        st.info("No processed data yet. Please switch to Admin mode, upload & process first.")
    else:
        # Doctor list
        doc_col = [c for c in df.columns if c.lower() == "docname"]
        doc_col = doc_col[0] if doc_col else df.columns[0]
        doctors = sorted(df[doc_col].dropna().unique().tolist())

        # Slider style selection
        selected_doc = st.select_slider(
            "Slide to select Doctor",
            options=doctors,
            value=doctors[0],
        )

        sel_df = df[df[doc_col] == selected_doc].copy()
        sel_df = sel_df.sort_values(["Year", "MonthNum"])

        # Remove MonthNum and show clean
        view_cols = ["Year", "Month", "Visits", "Consultation", "Medicines", "Procedure", "Other", "Avg_per_Visit"]
        sel_df = sel_df[view_cols]

        st.success(f"Doctor: **{selected_doc}**")
        st.dataframe(sel_df, use_container_width=True)

        xbytes = to_excel_bytes(sel_df, selected_doc)
        st.download_button(
            "Download Selected Doctor (xlsx)",
            data=xbytes,
            file_name=f"doc_performance_{selected_doc}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
