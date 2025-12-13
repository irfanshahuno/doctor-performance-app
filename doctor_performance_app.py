import calendar
import io

import pandas as pd
import streamlit as st

# ========================= App setup =========================
st.set_page_config(page_title="Doctor Performance — Monthwise", layout="wide")
st.title("Doctor Performance — Monthwise")

REQUIRED_CANON = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

st.caption("Required columns: " + ", ".join(REQUIRED_CANON))

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
    """
    Find a column in df matching target (case-insensitive, trimmed).
    Returns the actual column name if found, else None.
    """
    target_l = target.lower().strip()
    for c in df.columns:
        if str(c).lower().strip() == target_l:
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file) -> pd.DataFrame:
    # Always read first sheet; openpyxl is robust for .xlsx
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def write_excel_download(df: pd.DataFrame, file_name: str, sheet_name: str = "Doctor_Month_Summary") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=sheet_name)
    return buf.getvalue()

# ========================= UI: Upload =========================
src = st.file_uploader("Upload Excel (.xlsx) with required columns", type=["xlsx"])

if not src:
    st.info("Upload a .xlsx file to begin.")
    st.stop()

# ========================= Process =========================
try:
    df = load_excel(src)
except Exception as e:
    st.error(f"Failed to read Excel: {e}")
    st.stop()

# Clean column names
df = normalize_colnames(df)

# Resolve actual column names, case-insensitive
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
    st.error(f"Missing required column(s): {missing}")
    st.stop()

# ---- Unique visits (treat VisitNo as text to avoid 1 vs 1.0 issues) ----
df[col_VisitNo] = df[col_VisitNo].astype(str).str.strip()
df = df.drop_duplicates(subset=[col_VisitNo])

# ---- Parse VisitDate safely ----
dt = pd.to_datetime(df[col_VisitDate], errors="coerce")
df["Year"] = dt.dt.year
df["MonthNum"] = dt.dt.month.astype("Int64")
df["Month"] = df["MonthNum"].apply(safe_month_label)

# ---- Standardize Item Group into buckets ----
# Make a normalized key for mapping (strip+title)
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

# ---- Amount ----
df["Amount"] = pd.to_numeric(df[col_ActivityIns], errors="coerce").fillna(0)

# ========================= Aggregations =========================
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

# Ensure all expected bucket columns exist
for col in ["Consultation", "Medicines", "Procedure", "Other"]:
    if col not in piv.columns:
        piv[col] = 0

# Visits = distinct VisitNo per Doc/Year/Month
visits = (
    df.groupby([col_DocName, "Year", "MonthNum", "Month"])[col_VisitNo]
      .nunique()
      .reset_index(name="Visits")
)

out = piv.merge(visits, on=[col_DocName, "Year", "MonthNum", "Month"], how="left")

# Totals & Average per Visit (rounded to whole number)
out["Row_Total"] = out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1)
out["Avg_per_Visit"] = (out["Row_Total"] / out["Visits"].replace(0, pd.NA)).round(0).fillna(0).astype(int)

# Order months Jan..Dec inside each doctor/year
out = out.sort_values(by=[col_DocName, "Year", "MonthNum"], ascending=[True, True, True])

# Reorder columns nicely
final_cols = [
    col_DocName, "Year", "MonthNum", "Month",
    "Visits", "Consultation", "Medicines", "Procedure", "Other",
    "Row_Total", "Avg_per_Visit",
]
# Only keep columns that actually exist (defensive)
final_cols = [c for c in final_cols if c in out.columns]
out = out[final_cols]

st.success("Processed successfully ✅")

# Show table
st.dataframe(out, use_container_width=True)

# Optional quick debug
with st.expander("Debug (types & sample)", expanded=False):
    st.write(out.dtypes)
    st.write(out.head(5))

# ========================= Download =========================
xlsx_bytes = write_excel_download(out, "doc_performance.xlsx")
st.download_button(
    "Download Doctor Performance (xlsx)",
    data=xlsx_bytes,
    file_name="doc_performance.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

