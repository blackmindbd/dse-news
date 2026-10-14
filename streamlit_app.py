import glob
import io
import os
import re
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="DSE-News Dashboard", page_icon="📈", layout="wide"
)

st.title("📈 DSE News Dashboard – Equity Research Portal")


def process_sector_dataframe(df_sector):
    """Processes DataFrame to map Trading Code to Sector/Business Segment."""
    if "TRADING CODE" not in [str(c).strip().upper() for c in df_sector.columns]:
        header_row_idx = None
        for idx, row in df_sector.iterrows():
            if any(
                "TRADING CODE" in str(v).upper()
                for v in row.values
                if pd.notna(v)
            ):
                header_row_idx = idx
                break
        if header_row_idx is not None:
            df_sector.columns = [
                str(c).strip().upper() for c in df_sector.iloc[header_row_idx]
            ]
            df_sector = df_sector.iloc[header_row_idx + 1 :].reset_index(
                drop=True
            )

    code_col = [
        c
        for c in df_sector.columns
        if str(c).strip().upper() in ["TRADING CODE", "TRADING_CODE", "CODE"]
    ]
    sector_col = [
        c
        for c in df_sector.columns
        if str(c).strip().upper() in ["BUSINESS SEGMENT", "SECTOR", "SEGMENT"]
    ]

    if code_col and sector_col:
        sector_map = (
            df_sector[[code_col[0], sector_col[0]]]
            .dropna()
            .set_index(code_col[0])[sector_col[0]]
            .to_dict()
        )
        return {
            str(k).strip().upper(): str(v).strip()
            for k, v in sector_map.items()
        }
    return {}


def load_sector_mapping(uploaded_file=None):
    """Loads sector mapping from an uploaded file or local directory file."""
    try:
        if uploaded_file is not None:
            df_sector = pd.read_excel(uploaded_file)
            return process_sector_dataframe(df_sector)

        possible_files = (
            glob.glob("Sector*.xlsx")
            + glob.glob("*instruments*.xlsx")
            + glob.glob("Sector*.xls")
            + glob.glob("*instruments*.xls")
        )

        if possible_files:
            sector_file = possible_files[0]
            df_sector = pd.read_excel(sector_file)
            return process_sector_dataframe(df_sector)

        st.sidebar.info("💡 Upload 'Sector wise instruments' Excel file above.")
        return {}
    except Exception as e:
        st.sidebar.warning(f"Sector File Warning: {e}")
        return {}


@st.cache_data(ttl=300)
def fetch_dse_news():
    """Fetches live market news with fallback error handling."""
    try:
        from bdshare import get_news

        df = get_news(news_type="all")
        if df is None or df.empty:
            return pd.DataFrame(), "No news returned from DSE server."

        date_col = next(
            (
                c
                for c in ["date", "post_date", "time", "Date", "PostDate"]
                if c in df.columns
            ),
            None,
        )

        if date_col:
            df["Parsed_Date"] = pd.to_datetime(
                df[date_col], errors="coerce"
            ).dt.date
        else:
            df["Parsed_Date"] = None

        return df, None
    except Exception as e:
        return pd.DataFrame(), f"Network/Server Error: {e}"


def combine_valid_values(series):
    """Pick the first non-N/A non-null string value from a grouped series."""
    valid_vals = [
        str(v).strip()
        for v in series
        if pd.notna(v) and str(v).strip() != "" and str(v).strip().upper() != "N/A"
    ]
    return valid_vals[0] if valid_vals else "N/A"


def combine_valid_floats(series):
    """Pick the first valid float numeric value from a grouped series."""
    valid_vals = [v for v in series if pd.notna(v)]
    return valid_vals[0] if valid_vals else None


def parse_date_to_standard(date_str):
    """Converts date strings (e.g. 'January 16, 2027' or '16.01.2027') safely to 'DD.MM.YYYY'."""
    date_str = str(date_str).strip()
    try:
        dt = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(dt):
            return dt.strftime("%d.%m.%Y")
    except Exception:
        pass
    return date_str if date_str else "N/A"


def extract_report_data(df, sector_map):
    """Extracts financial details, sector mappings, AGM/Record Dates, converts EPS to float, merges rows, and filters empty rows."""
    if df is None or df.empty:
        return pd.DataFrame()

    report_rows = []

    title_col = next(
        (
            c
            for c in ["title", "NewsTitle", "Title", "Announcement"]
            if c in df.columns
        ),
        None,
    )
    symbol_col = next(
        (
            c
            for c in ["symbol", "TradingCode", "code", "Symbol"]
            if c in df.columns
        ),
        None,
    )
    news_col = next(
        (c for c in ["news", "NewsDetails", "Details"] if c in df.columns), None
    )
    date_col = next(
        (
            c
            for c in ["date", "post_date", "Parsed_Date", "Date"]
            if c in df.columns
        ),
        None,
    )

    date_pattern = r"(?:[A-Za-z]+\s+\d{1,2},\s*\d{4}|\d{1,2}[\.\/\-]\d{1,2}[\.\/\-]\d{2,4})"

    for _, row in df.iterrows():
        title = str(row.get(title_col, "")) if title_col else ""
        text = str(row.get(news_col, "")) if news_col else ""
        full_content = f"{title} {text}"

        # 1. Date formatting (DD-MM-YY)
        raw_date = row.get(date_col, "")
        try:
            formatted_date = pd.to_datetime(raw_date).strftime("%d-%m-%y")
        except Exception:
            formatted_date = str(raw_date)

        # 2. Trading Code & Sector Mapping
        trading_code = str(row.get(symbol_col, "")).strip() if symbol_col else ""
        sector = sector_map.get(trading_code.upper(), "N/A")

        # 3. Category Determination
        title_lower = title.lower()
        content_lower = full_content.lower()

        category = "General"
        if "q3 financials" in title_lower:
            category = "Q3 Financials"
        elif "dividend" in content_lower:
            category = "Dividend Declaration"
        elif "category change" in title_lower:
            category = "Category Change"
        elif "price limit" in content_lower:
            category = "Price Limit Open"

        # 4. Extract Dividend Recommendation
        div_match = re.search(
            r"(\d+(?:\.\d+)?%\s*(?:Cash|Stock|Bonus))|((?:No|nil)\s*dividend)",
            full_content,
            re.IGNORECASE,
        )
        dividend_rec = div_match.group(0) if div_match else "N/A"

        # 5. Extract New EPS as Float
        new_eps = None
        new_eps_match = re.search(
            r"(?:EPS\s+of\s+Tk\.|EPS\s+was\s+Tk\.|EPS\s+Tk\.)\s*(\(?-?\d+(?:\.\d+)?\)?)",
            full_content,
            re.IGNORECASE,
        )
        if new_eps_match:
            val = new_eps_match.group(1).strip()
            try:
                if val.startswith("(") and val.endswith(")"):
                    new_eps = -float(val[1:-1])
                else:
                    new_eps = float(val)
            except ValueError:
                new_eps = None

        # 6. Extract Same Period Last Year EPS as Float
        last_eps = None
        last_eps_match = re.search(
            r"as\s+against\s+(?:EPS\s+of\s+Tk\.|EPS\s+was\s+Tk\.|EPS\s+Tk\.|Tk\.)\s*(\(?-?\d+(?:\.\d+)?\)?)",
            full_content,
            re.IGNORECASE,
        )
        if last_eps_match:
            val = last_eps_match.group(1).strip()
            try:
                if val.startswith("(") and val.endswith(")"):
                    last_eps = -float(val[1:-1])
                else:
                    last_eps = float(val)
            except ValueError:
                last_eps = None

        # 7. Extract Date of AGM
        agm_date = "N/A"
        agm_match = re.search(
            r"Date\s+of\s+AGM\b[:\s]*(" + date_pattern + r")",
            full_content,
            re.IGNORECASE,
        )
        if agm_match:
            agm_date = parse_date_to_standard(agm_match.group(1))

        # 8. Extract Record Date
        record_date = "N/A"
        record_match = re.search(
            r"Record\s+Date\b[:\s]*(" + date_pattern + r")",
            full_content,
            re.IGNORECASE,
        )
        if record_match:
            record_date = parse_date_to_standard(record_match.group(1))

        report_rows.append(
            {
                "Date": formatted_date,
                "Trading Code": trading_code,
                "Sector": sector,
                "Category": category,
                "Dividend Recommendation": dividend_rec,
                "New EPS": new_eps,
                "Same Period Last Year EPS": last_eps,
                "Date of AGM": agm_date,
                "Record Date": record_date,
            }
        )

    raw_report_df = pd.DataFrame(report_rows)

    if raw_report_df.empty:
        return raw_report_df

    # Group and merge matching Date, Trading Code, Sector, & Category
    merged_df = (
        raw_report_df.groupby(
            ["Date", "Trading Code", "Sector", "Category"], as_index=False
        )
        .agg(
            {
                "Dividend Recommendation": combine_valid_values,
                "New EPS": combine_valid_floats,
                "Same Period Last Year EPS": combine_valid_floats,
                "Date of AGM": combine_valid_values,
                "Record Date": combine_valid_values,
            }
        )
    )

    # Filter out empty rows
    valid_rows_mask = ~(
        (merged_df["Dividend Recommendation"] == "N/A")
        & (merged_df["New EPS"].isna())
        & (merged_df["Same Period Last Year EPS"].isna())
        & (merged_df["Date of AGM"] == "N/A")
        & (merged_df["Record Date"] == "N/A")
    )
    cleaned_df = merged_df[valid_rows_mask].reset_index(drop=True)

    return cleaned_df


# --- SIDEBAR CONTROL & DATA FETCHING ---
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# File uploader for sector map
st.sidebar.header("📁 Sector Mapping File")
uploaded_sector_file = st.sidebar.file_uploader(
    "Upload 'Sector wise instruments' File", type=["xlsx", "xls"]
)

sector_map = load_sector_mapping(uploaded_sector_file)

if sector_map:
    st.sidebar.success(f"Loaded {len(sector_map)} stock-to-sector mappings!")

with st.spinner("Connecting to Dhaka Stock Exchange..."):
    df, error = fetch_dse_news()

if error:
    st.sidebar.error(f"Feed Warning: {error}")

if df is None or df.empty:
    st.warning("Could not pull live feed from server. Check your network connection.")
    st.stop()

filtered_df = df.copy()

# 1. DATE FILTER
st.sidebar.header("📅 Filter by Date")
if "Parsed_Date" in filtered_df.columns and filtered_df["Parsed_Date"].notna().any():
    min_date = filtered_df["Parsed_Date"].min()
    max_date = filtered_df["Parsed_Date"].max()

    date_mode = st.sidebar.radio(
        "Date Selection Mode", ["All Dates", "Single Date", "Date Range"], index=1
    )

    if date_mode == "Single Date":
        selected_date = st.sidebar.date_input(
            "Select Date", max_date, min_value=min_date, max_value=max_date
        )
        filtered_df = filtered_df[filtered_df["Parsed_Date"] == selected_date]
    elif date_mode == "Date Range":
        date_range = st.sidebar.date_input(
            "Select Date Range",
            (min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            filtered_df = filtered_df[
                (filtered_df["Parsed_Date"] >= date_range[0])
                & (filtered_df["Parsed_Date"] <= date_range[1])
            ]

# 2. CATEGORY FILTER
title_col = next(
    (
        c
        for c in ["title", "NewsTitle", "Title", "Announcement"]
        if c in filtered_df.columns
    ),
    None,
)
st.sidebar.header("🎯 Filter by Category")
categories = [
    "Dividend",
    "Category Change",
    "Price Limit Open",
    "Q3 Financials",
]
selected_categories = st.sidebar.multiselect(
    "Select Categories:", options=categories, default=[]
)

if selected_categories and title_col:
    pattern = "|".join(selected_categories)
    filtered_df = filtered_df[
        filtered_df[title_col].str.contains(pattern, case=False, na=False)
    ]

# 3. STOCK SYMBOL FILTER
symbol_col = next(
    (
        c
        for c in ["symbol", "TradingCode", "code", "Symbol"]
        if c in filtered_df.columns
    ),
    None,
)
if symbol_col:
    symbols = sorted(
        [
            str(s)
            for s in filtered_df[symbol_col].dropna().unique()
            if str(s).strip()
        ]
    )
    selected_symbols = st.sidebar.multiselect("Filter by Stock Symbol", symbols)
    if selected_symbols:
        filtered_df = filtered_df[
            filtered_df[symbol_col].astype(str).isin(selected_symbols)
        ]

# 4. KEYWORD SEARCH
custom_keyword = st.sidebar.text_input("Search Specific Keywords")
if custom_keyword:
    mask = pd.Series(False, index=filtered_df.index)
    for col in [c for c in filtered_df.columns if c != "Parsed_Date"]:
        mask |= (
            filtered_df[col]
            .astype(str)
            .str.contains(custom_keyword, case=False, na=False)
        )
    filtered_df = filtered_df[mask]

# --- MAIN DISPLAY MODE ---
view_mode = st.radio(
    "Select Display Mode",
    options=["📊 Custom Report View", "📰 Full News Feed"],
    horizontal=True,
)

if view_mode == "📊 Custom Report View":
    st.subheader("Summary Report")
    try:
        report_df = extract_report_data(filtered_df, sector_map)

        if not report_df.empty:
            try:
                output_buffer = io.BytesIO()
                with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
                    report_df.to_excel(writer, index=False, sheet_name="DSE_Report")
                excel_data = output_buffer.getvalue()

                st.download_button(
                    label="📥 Download Excel Report (.xlsx)",
                    data=excel_data,
                    file_name=f"DSE_Report_{pd.Timestamp.now().strftime('%Y_%m_%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception:
                csv_data = report_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label="📥 Download CSV Report",
                    data=csv_data,
                    file_name=f"DSE_Report_{pd.Timestamp.now().strftime('%Y_%m_%d')}.csv",
                    mime="text/csv",
                )

            st.dataframe(report_df, use_container_width=True, hide_index=True)
        else:
            st.info("No records matched the current selection.")
    except Exception as err:
        st.error(f"Error generating report view: {err}")

else:
    st.subheader("Raw Feed Data")
    raw_display = filtered_df.drop(columns=["Parsed_Date"], errors="ignore")

    # Move the date column to the first column
    cols = list(raw_display.columns)
    raw_date_col = next(
        (c for c in ["date", "post_date", "time", "Date", "PostDate"] if c in cols),
        None,
    )
    if raw_date_col:
        cols.remove(raw_date_col)
        cols = [raw_date_col] + cols
        raw_display = raw_display[cols]

    st.dataframe(raw_display, use_container_width=True, hide_index=True)