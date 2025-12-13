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

def normalize_colnames(df):
    df.columns = [" ".join(str(c).strip().split()) for c in df.columns]
    return df

def find_col(df, *candidates):
    """
    Case-insensitive finder across multiple candidates.
    Accepts aliases like ("DocName","Doc Name","Doctor","Doctor Name","Provider").
    """
    wanted = {c.lower().strip(): c for c in candidates}
    for c in df.columns:
        key = str(c).lower().strip()
        if key in wanted:
            return c
    # heuristic: strip spaces and look for doc/doctor/provider tokens
    for c in df.columns:
        key = str(c).lower().replace(" ", "")
        if any(k in key for k in ["docname","doc","doctor","provider","physician"]):
            return c
    return None

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file, sheet_name=0, engine="openpyxl")

def process_file(df_in):
    df = normalize_colnames(df_in.copy())

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
    ig = df[g].astype(str).str.title()
    bucket_map = {
        "Consultation": "Consultation", "Consultations": "Consultation",
        "Medicine": "Medicines", "Medicines": "Medicines", "Drug": "Medicines", "Drugs": "Medicines",
        "Procedure": "Procedure", "Procedures": "Procedure",
    }
    df["Bucket"] = ig.map(bucket_map).fillna("Other")

    # Aggregate
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

    # Visits per month
    visits = df.groupby([n, "Year", "MonthNum", "Month"])[v].nunique().reset_index(name="Visits")
    out = piv.merge(visits, on=[n, "Year", "MonthNum", "Month"], how="left")

    # Avg per visit (no Row_Total kept for final view)
    out["Avg_per_Visit"] = (
        (out[["Consultation", "Medicines", "Procedure", "Other"]].sum(axis=1) / out["Visits"].replace(0, pd.NA))
        .round(0).fillna(0).astype(int)
    )

    out = out.sort_values([n, "Year", "MonthNum"]).reset_index(drop=True)

    # 🔒 Standardize the doctor column name so the View code always finds it
    out = out.rename(columns={n: "DocName"})

    return out

def to_excel_bytes(df, name="Doctor_Summary"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name=name)
    return buf.getvalue()

# ================== MODE ==================
mode = st.toggle("Admin mode", value=False, help="ON = upload & process; OFF = view")

# ================== ADMIN ==================
if mode:
    st.subheader("Admin — Upload & Process File")
    file = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
    cols = st.columns([1, 2])
    if cols[0].button("Process", type="primary"):
        if not file:
            st.error("Please upload a file first.")
        else:
            try:
                df = load_excel(file)
                result = process_file(df)
                st.session_state["processed_df"] = result
                st.success("✅ Processed and saved for viewing.")
                st.dataframe(result.head(10), use_container_width=True)
                xbytes = to_excel_bytes(result)
                st.download_button(
                    "Download Full Doctor Performance (xlsx)",
                    data=xbytes,
                    file_name="doc_performance_all.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"Error: {e}")

# ================== VIEW ==================
else:
    st.subheader("View — Doctor Monthwise Performance")
    df = st.session_state.get("processed_df")

    if df is None or df.empty:
        st.info("No processed data yet. Switch to Admin, upload & process first.")
    else:
        # Robust doctor column detection (now standardized to "DocName")
        doc_col = "DocName" if "DocName" in df.columns else next(iter(df.columns))
        doctors = sorted(df[doc_col].dropna().astype(str).unique().tolist())

        if not doctors:
            st.warning("No doctors found in processed data.")
        else:
            selected_doc = st.selectbox("Select Doctor", doctors, index=0)

            sel_df = df[df[doc_col] == selected_doc].copy().sort_values(["Year", "MonthNum"])
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
                use_container_width=True,
            )

