import pandas as pd, streamlit as st, calendar

REQUIRED = ["VisitNo", "VisitDate", "DocName", "Item Group", "ActivityIns"]

st.set_page_config(page_title="Doctor Performance", layout="wide")
st.title("Doctor Performance — Monthwise")

# -------- Upload --------
src = st.file_uploader("Upload Excel (.xlsx) with required columns", type=["xlsx"])
st.caption("Required columns: " + ", ".join(REQUIRED))

@st.cache_data(show_spinner=False)
def load_excel(file):
    return pd.read_excel(file)

def month_num_from_any(x):
    try:
        return pd.to_datetime(x).month
    except Exception:
        return 0

if src:
    try:
        df = load_excel(src)
        # basic column normalisation
        df.columns = [c.strip() for c in df.columns]
        missing = [c for c in REQUIRED if c not in df.columns]
        if missing:
            st.error(f"Missing columns: {missing}")
            st.stop()

        # unique visits
        df = df.drop_duplicates(subset=["VisitNo"])
        # year & month
        df["Year"] = pd.to_datetime(df["VisitDate"], errors="coerce").dt.year
        df["MonthNum"] = pd.to_datetime(df["VisitDate"], errors="coerce").dt.month
        df["Month"] = df["MonthNum"].apply(lambda n: calendar.month_abbr[n] if pd.notna(n) and n in range(1,13) else "")

        # map Item Group to the 4 buckets we care about
        bucket_map = {
            "Consultation": "Consultation",
            "Medicines": "Medicines",
            "Medicine": "Medicines",
            "Procedure": "Procedure",
        }
        df["Bucket"] = df["Item Group"].map(bucket_map).fillna("Other")

        # amount per row (ActivityIns is your claimable/paid)
        df["Amount"] = pd.to_numeric(df["ActivityIns"], errors="coerce").fillna(0)

        # aggregate
        piv = df.pivot_table(
            index=["DocName", "Year", "MonthNum", "Month"],
            columns="Bucket",
            values="Amount",
            aggfunc="sum",
            fill_value=0,
        ).reset_index()

        # make sure all expected columns exist
        for col in ["Consultation","Medicines","Procedure","Other"]:
            if col not in piv.columns: piv[col] = 0

        # visits = distinct VisitNo per Doc/Year/Month
        v = (df.groupby(["DocName","Year","MonthNum","Month"])["VisitNo"]
                .nunique()
                .reset_index(name="Visits"))
        out = piv.merge(v, on=["DocName","Year","MonthNum","Month"], how="left")

        # totals & avg
        out["Row_Total"] = out[["Consultation","Medicines","Procedure","Other"]].sum(axis=1)
        out["Avg_per_Visit"] = (out["Row_Total"] / out["Visits"].replace(0, pd.NA)).round(0).fillna(0).astype(int)

        # order months Jan..Dec and doctors grouped
        out = out.sort_values(["DocName","Year","MonthNum"], ascending=[True, True, True])

        st.success("Processed successfully ✅")
        st.dataframe(out, use_container_width=True)

        # Download button
        st.download_button(
            "Download Doctor Performance (xlsx)",
            data=pd.ExcelWriter, # placeholder to keep structure simple
            file_name="doc_performance.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            disabled=True,
            help="(Tip) For Excel download, I can add write-out in next step if you need."
        )

    except Exception as e:
        st.error(f"Error: {e}")
else:
    st.info("Upload a .xlsx file to begin.")
