"""Data-backed RentalShield Analytics page helpers.

Reads the validated outputs from rentalshield_eda.py. Counts remain descriptive;
this module does not infer whether a property is safe or a listing is fraudulent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


AREA_LABELS = {
    "Central Business District": "Downtown (CBD crime source)",
    "Downtown": "Downtown",
    "West End": "West End",
}
BEDROOM_LABELS = {"1": "1 bedroom", "2": "2 bedrooms", "3": "3+ bedrooms"}
MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        if pd.isna(value):
            return default
        result = float(str(value).replace(",", "").strip())
        if not pd.notna(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _is_true(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y"})


def _money(value: float | int | None) -> str:
    return "Not available" if value is None else f"${value:,.0f}"


def build_analytics_context(base_dir: Path, query_args=None) -> dict[str, Any]:
    """Build template context for the /analytics route; gracefully handles missing outputs."""
    output_dir = Path(base_dir) / "analytics" / "eda_outputs"
    query_args = query_args or {}

    # Query parameters: safe bounded values only.
    try:
        selected_year = int(query_args.get("year", "2025"))
    except (TypeError, ValueError):
        selected_year = 2025
    if selected_year not in (2023, 2024, 2025, 2026):
        selected_year = 2025

    selected_area = str(query_args.get("area", "all")).lower()
    if selected_area not in ("all", "downtown", "west_end"):
        selected_area = "all"
    exclude_duplicates = str(query_args.get("exclude_duplicates", "0")).lower() in ("1", "true", "yes", "on")

    crime = _read_csv(output_dir / "police_crime_combined.csv")
    yearly_file = _read_csv(output_dir / "crime_counts_by_area_year.csv")
    type_file = _read_csv(output_dir / "crime_counts_by_area_type.csv")
    address_summary = _read_csv(output_dir / "property_address_area_summary.csv")
    benchmarks_df = _read_csv(output_dir / "rent_benchmarks_extracted.csv")
    summary_df = _read_csv(output_dir / "eda_summary.csv")
    duplicate_audit = _read_csv(output_dir / "crime_duplicate_audit_by_file.csv")
    fraud_profile = _read_csv(output_dir / "cafc_fraud_data_profile.csv")

    # Normalize crime dataset fields expected by the EDA output.
    if not crime.empty:
        if "incident_date" not in crime.columns:
            if {"YEAR", "MONTH", "DAY"}.issubset(crime.columns):
                crime["incident_date"] = pd.to_datetime(
                    {"year": crime["YEAR"], "month": crime["MONTH"], "day": crime["DAY"]},
                    errors="coerce",
                )
            else:
                crime["incident_date"] = pd.NaT
        else:
            crime["incident_date"] = pd.to_datetime(crime["incident_date"], errors="coerce")
        if "source_area" not in crime.columns:
            crime["source_area"] = "Unknown"
        if "TYPE" not in crime.columns:
            crime["TYPE"] = "Unknown"
        if "is_exact_duplicate_record" not in crime.columns:
            crime["is_exact_duplicate_record"] = False
        if "is_privacy_protected_location" not in crime.columns:
            crime["is_privacy_protected_location"] = False

        crime["is_exact_duplicate_record"] = _is_true(crime["is_exact_duplicate_record"])
        crime["is_privacy_protected_location"] = _is_true(crime["is_privacy_protected_location"])
        crime["source_year"] = pd.to_numeric(
            crime.get("source_year", crime["incident_date"].dt.year), errors="coerce"
        )
        crime["month_num"] = crime["incident_date"].dt.month
        crime["area_label"] = crime["source_area"].replace(AREA_LABELS)

    total_crime_rows = int(len(crime)) if not crime.empty else 0
    duplicate_rows_flagged = int(crime["is_exact_duplicate_record"].sum()) if not crime.empty else 0
    privacy_rows = int(crime["is_privacy_protected_location"].sum()) if not crime.empty else 0
    latest_date = None
    earliest_date = None
    if not crime.empty and crime["incident_date"].notna().any():
        earliest_date = crime["incident_date"].min().strftime("%b %d, %Y")
        latest_date = crime["incident_date"].max().strftime("%b %d, %Y")

    chart_crime = crime.copy()
    if exclude_duplicates and not chart_crime.empty:
        chart_crime = chart_crime[~chart_crime["is_exact_duplicate_record"]].copy()
    chart_count = len(chart_crime)
    chart_area_keys = ["Central Business District", "West End"]
    chart_area_names = [AREA_LABELS[key] for key in chart_area_keys]
    chart_years = [2023, 2024, 2025, 2026]
    yearly_group = pd.DataFrame()
    if not chart_crime.empty:
        yearly_group = (
            chart_crime.groupby(["source_area", "source_year"]).size().rename("count").reset_index()
        )
    annual_chart = {
        "labels": ["2023", "2024", "2025", "2026 YTD*"],
        "datasets": [],
    }
    for area in chart_area_keys:
        values = []
        for year in chart_years:
            matching = yearly_group[
                (yearly_group["source_area"] == area) & (yearly_group["source_year"] == year)
            ] if not yearly_group.empty else pd.DataFrame()
            values.append(int(matching["count"].iloc[0]) if not matching.empty else 0)
        annual_chart["datasets"].append({"label": AREA_LABELS[area], "data": values})

    # Monthly trend for the selected year and chosen area(s).
    month_rows = chart_crime[chart_crime["incident_date"].dt.year == selected_year].copy() if not chart_crime.empty else pd.DataFrame()
    if selected_area == "downtown":
        month_rows = month_rows[month_rows["source_area"] == "Central Business District"] if not month_rows.empty else month_rows
        trend_areas = ["Central Business District"]
    elif selected_area == "west_end":
        month_rows = month_rows[month_rows["source_area"] == "West End"] if not month_rows.empty else month_rows
        trend_areas = ["West End"]
    else:
        trend_areas = chart_area_keys
    month_chart = {"labels": MONTH_LABELS, "datasets": []}
    for area in trend_areas:
        values = []
        for month in range(1, 13):
            matched = month_rows[(month_rows["source_area"] == area) & (month_rows["month_num"] == month)] if not month_rows.empty else pd.DataFrame()
            values.append(int(len(matched)))
        month_chart["datasets"].append({"label": AREA_LABELS.get(area, area), "data": values})

    # Top crime categories based on the same filter state as the trend chart.
    category_rows = chart_crime.copy()
    if selected_area == "downtown" and not category_rows.empty:
        category_rows = category_rows[category_rows["source_area"] == "Central Business District"]
    elif selected_area == "west_end" and not category_rows.empty:
        category_rows = category_rows[category_rows["source_area"] == "West End"]
    if not category_rows.empty:
        category_group = category_rows.groupby("TYPE").size().sort_values(ascending=False).head(8).sort_values()
        category_chart = {"labels": [str(x) for x in category_group.index], "data": [int(x) for x in category_group.values]}
    else:
        category_chart = {"labels": [], "data": []}

    # Completed-year comparison (exclude the partial 2026 year from the percentage calculation).
    def yearly_count(area: str, year: int) -> int:
        if yearly_group.empty:
            return 0
        matched = yearly_group[(yearly_group["source_area"] == area) & (yearly_group["source_year"] == year)]
        return int(matched["count"].iloc[0]) if not matched.empty else 0

    year_change = []
    for area in chart_area_keys:
        first, last = yearly_count(area, 2023), yearly_count(area, 2025)
        pct = ((last - first) / first * 100) if first else None
        year_change.append({
            "area": AREA_LABELS[area], "count_2023": first, "count_2025": last,
            "percent_change": None if pct is None else round(pct, 1),
        })

    # Address coverage EDA output.
    address_cards = []
    if not address_summary.empty:
        for _, row in address_summary.iterrows():
            label = str(row.get("analysis_area", "Unknown"))
            address_cards.append({
                "area": label,
                "rows": int(_number(row.get("rows"), 0) or 0),
                "unique_addresses": int(_number(row.get("unique_addresses"), 0) or 0),
                "missing_addresses": int(_number(row.get("missing_canonical_address"), 0) or 0),
            })

    # Rent benchmark table and optional user-entered comparison.
    benchmarks = []
    benchmark_lookup = {}
    if not benchmarks_df.empty:
        for _, row in benchmarks_df.iterrows():
            key = str(row.get("bedroom_category", ""))
            raw_value = str(row.get("asking_rent_raw", ""))
            value = _number(row.get("asking_rent_numeric"))
            geography = str(row.get("geography", "Vancouver CMA"))
            if value is not None and value > 0:
                item = {"bedroom_category": key, "value": value, "display_value": _money(value), "geography": geography, "raw": raw_value}
                benchmarks.append(item)
                benchmark_lookup[key] = value
    bedroom_input = str(query_args.get("bedrooms", "2"))
    if bedroom_input not in BEDROOM_LABELS:
        bedroom_input = "2"
    rent_text = str(query_args.get("rent", "")).strip()
    rent_comparison = None
    entered_rent = _number(rent_text)
    benchmark_key = BEDROOM_LABELS[bedroom_input]
    matched_benchmark = benchmark_lookup.get(benchmark_key)
    if entered_rent is not None and 0 < entered_rent <= 100000 and matched_benchmark:
        difference = entered_rent - matched_benchmark
        rent_comparison = {
            "entered_rent": entered_rent,
            "benchmark": matched_benchmark,
            "difference": difference,
            "difference_display": _money(abs(difference)),
            "percent_difference": round(difference / matched_benchmark * 100, 1),
            "direction": "above" if difference > 0 else "below" if difference < 0 else "equal to",
            "bedroom_label": benchmark_key,
        }

    # Fraud profile: count only; avoid using national data as property-level evidence.
    fraud_records = None
    fraud_categories = None
    if not summary_df.empty:
        fraud_rows = summary_df[summary_df.get("dataset", pd.Series(dtype=str)).astype(str).str.contains("cafc_fraud_reporting", case=False, na=False)]
        if not fraud_rows.empty:
            fraud_records = int(_number(fraud_rows.iloc[0].get("rows"), 0) or 0)
    if not fraud_profile.empty and {"column", "unique_values"}.issubset(fraud_profile.columns):
        row = fraud_profile[fraud_profile["column"].astype(str).str.contains("Fraud and Cybercrime Thematic Categories", case=False, na=False)]
        if not row.empty:
            fraud_categories = int(_number(row.iloc[0].get("unique_values"), 0) or 0)

    quality_table = []
    if not duplicate_audit.empty:
        for _, row in duplicate_audit.iterrows():
            quality_table.append({
                "source_file": str(row.get("source_file", "")),
                "rows": int(_number(row.get("rows"), 0) or 0),
                "duplicates": int(_number(row.get("exact_duplicate_rows"), 0) or 0),
                "privacy_rows": int(_number(row.get("privacy_protected_location_rows"), 0) or 0),
            })

    data_ready = not crime.empty and bool(benchmarks) and bool(address_cards)
    return {
        "data_ready": data_ready,
        "output_dir_exists": output_dir.exists(),
        "total_crime_rows": total_crime_rows,
        "chart_crime_rows": int(chart_count),
        "duplicate_rows_flagged": duplicate_rows_flagged,
        "privacy_rows": privacy_rows,
        "earliest_date": earliest_date,
        "latest_date": latest_date,
        "selected_year": selected_year,
        "selected_area": selected_area,
        "exclude_duplicates": exclude_duplicates,
        "annual_chart_json": annual_chart,
        "month_chart_json": month_chart,
        "category_chart_json": category_chart,
        "year_change": year_change,
        "address_cards": address_cards,
        "benchmarks": benchmarks,
        "rent_text": rent_text,
        "bedroom_input": bedroom_input,
        "rent_comparison": rent_comparison,
        "fraud_records": fraud_records,
        "fraud_categories": fraud_categories,
        "quality_table": quality_table,
        "missing_files": [name for name in [
            "police_crime_combined.csv", "crime_counts_by_area_year.csv",
            "property_address_area_summary.csv", "rent_benchmarks_extracted.csv",
        ] if not (output_dir / name).exists()],
    }
