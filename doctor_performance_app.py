import calendar
import io
import pandas as pd
import streamlit as st

# ========================= App setup =========================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")

st.title("Doctor Performance — Monthwise")
st.caption("Toggle Admin to upload & process. Switch to View to select a doctor and review results.")

REQUIRED_CANON = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

# -------- persist processed dataframe across modes --------
if "processed_df" not in st.session_state:
    st.session_state["processed_df"] = None

# ========================= Helpers =========================
def safe_month_label(n):
    """Return 'Jan', 'Feb', ... or '' if invalid/NaN/float."""
    try:
        n = int(float(n))
        return calendar.month_abbr[n] if 1 <= n <= 12 else ""
    except Exception:
        return ""

def normalize_colnames(df: pd.DataFrame) -> pd.DataFrame:
    """Strip and unify duplicate whitespace in column names."""
    newcols = []
    for c in df.columns:
        if not isinstance(c, str):
            c = str(c)
        c = " ".join(c.strip().split())
        newcols.append(c)
    df.columns = newcols
    return df

def find_column(df: pd.DataFrame, target: str) -> str | None:
    """Case-insensitive exact match for a target column name."""
    target_l = target.lower().strip()
    for c in df.columns:
        if str(c).lower().strip() == target_l:
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file) -> pd.DataFrame:
    # Read the first sheet explicitly
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def build_doctor_month_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Return month-wise totals per doctor with Visits and Avg/Visit, no Row_Total/MonthNum in final view."""
    df = normalize_colnames(df)

    # Resolve actual column names
    col_VisitNo     = find_column(df, "VisitNo")
    col_VisitDate   = find_column(df, "VisitDate")
    col_DocName     = find_column(df, "DocName")
    col_ItemGroup   = find_column(df, "Item Group")
    col_ActivityIns = find_column(df, "ActivityIns")

    missing = []
    for name, col in [
        ("VisitNo", col_VisitNo),
        ("VisitDate", col_VisitDate),
        ("DocName", col_DocName),
        ("Item Group", col_ItemGroup),
        ("ActivityIns", col_ActivityIns),
    ]:
        if col is None:
            missing.append(name)
    if missing:
        raise ValueError(f"Missing required column(s): {missing}")

    # Unique visits
    df[col_VisitNo] = df[col_VisitNo].astype(str).str.strip()
    df = df.drop_duplicates(subset=[col_VisitNo])

    # Parse dates
    dt = pd.to_datetime(df[col_VisitDate], errors="coerce")
    df["Year"] = dt.dt.year
    df["MonthNum"] = dt.dt.month.astype("Int64")
    df["Month"] = df["MonthNum"].apply(safe_month_label)

    # Buckets
    ig_norm = df[col_ItemGroup].astype(str).str.strip().str.title()
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
    df["Bucket"] = ig_norm.map(bucket_map).fillna("Other")

    # Amount
    df["Amount"] = pd.to_numeric(df[col_ActivityIns], errors="coerce").fillna(0)

    # Sum amount by Doc/Year/Month × Bucket
    piv = (
        df.pivot_table(
            index=[col_DocName, "Year", "MonthNum", "Month"],
            columns="Bucket",
            values="Amount",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )

    # Ensure expected bucket columns
    for col in ["Consultation", "Medicines", "Procedure", "Other"]:
        if col not in piv.columns:
            piv[col] = 0

    # Visits (distinct VisitNo)
    visits = (
        df.groupby([col_DocName, "Year", "MonthNum", "Month"])[col_VisitNo]
          .nunique()
          .reset_index(name="Visits")
    )

    out = piv.merge(visits, on=[col_DocName, "Year", "MonthNum", "Month"], how="left")

    # Average per Visit (hide Row_Total later as requested)
    row_total = out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1)
    out["Avg_per_Visit"] = (row_total / out["Visits"].replace(0, pd.NA)).round(0).fillna(0).astype(int)

    # Sort within each Doctor/Year by month
    out = out.sort_values(by=[col_DocName, "Year", "MonthNum"], ascending=[True, True, True])

    # Final column order for VIEW (no MonthNum, no Row_Total)
    final_cols = [
        col_DocName, "Year", "Month",
        "Visits", "Consultation", "Medicines", "Procedure", "Other",
        "Avg_per_Visit",
    ]
    # Ensure we only keep existing columns defensively
    final_cols = [c for c in final_cols if c in out.columns]
    return out[final_cols].reset_index(drop=True)

def to_xlsx_bytes(df: pd.DataFrame, file_name: str, sheet_name: str = "Doctor_Month_Summary") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet_name)
    return buf.getvalue()

# ========================= Mode Toggle =========================
mode = st.toggle("Admin mode", value=False, help="Turn ON to upload & process; OFF to view the results.")

# ========================= ADMIN =========================
if mode:
    st.subheader("Admin — Upload & Process")
    src = st.file_uploader("Upload Excel (.xlsx) with required columns", type=["xlsx"])
    st.caption("Required columns: " + ", ".join(REQUIRED_CANON))

    colA, colB = st.columns([1, 2])
    with colA:
        process = st.button("Process", type="primary", use_container_width=True,
                            help="Process the uploaded file and prepare the month-wise doctor summary.")

    if process:
        if not src:
            st.error("Please upload a .xlsx file first.")
        else:
            try:
                raw = load_excel(src)
                processed = build_doctor_month_summary(raw)
                st.session_state["processed_df"] = processed
                st.success("✅ Processed and saved for viewing.")
                # Show small preview
                st.dataframe(processed.head(20), use_container_width=True)

                # Full download
                xbytes = to_xlsx_bytes(processed, "doc_performance_all.xlsx")
                st.download_button(
                    "Download FULL Doctor Performance (xlsx)",
                    data=xbytes,
                    file_name="doc_performance_all.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Processing failed: {e}")

# ========================= VIEW =========================
else:
    st.subheader("View — Doctor Monthwise Performance")
    processed_df = st.session_state.get("processed_df", None)

    if processed_df is None or processed_df.empty:
        st.info("No processed data yet. Ask Admin to upload and click **Process**.")
    else:
        doc_col = [c for c in processed_df.columns if c.lower() == "docname"]
        doc_col = doc_col[0] if doc_col else processed_df.columns[0]  # fallback
        doctors = sorted(processed_df[doc_col].dropna().astype(str).unique())
        selected_doc = st.selectbox("Select Doctor", doctors, index=0)

        view_df = processed_df[processed_df[doc_col] == selected_doc].copy()
        # Sort for viewer (Year then Month order via a hidden key)
        month_order = {calendar.month_abbr[i]: i for i in range(1, 13)}
        view_df["_m"] = view_df["Month"].map(month_order).fillna(0).astype(int)
        view_df = view_df.sort_values(["Year", "_m"]).drop(columns=["_m"])

        st.dataframe(view_df, use_container_width=True)

        # Download selected doctor’s slice
        xbytes_sel = to_xlsx_bytes(view_df, "doc_performance_selected.xlsx", sheet_name=selected_doc[:30])
        st.download_button(
            "Download Selected Doctor (xlsx)",
            data=xbytes_sel,
            file_name=f"doc_performance_{selected_doc}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
