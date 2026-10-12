"""RentalShield EDA pipeline.

Place this file in the project root beside app.py.
Run: python rentalshield_eda.py

Inputs are read from ./data and outputs are written to ./analytics/eda_outputs.
This is exploratory analysis only: it does not label a property safe or fraudulent.
"""
from pathlib import Path
import csv
import re
import warnings

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
CRIME_DIR = DATA_DIR / "police_crime" / "extracted_csvs"
OUTPUT_DIR = PROJECT_ROOT / "analytics" / "eda_outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def write_csv(df, filename):
    df.to_csv(OUTPUT_DIR / filename, index=False)


def load_csv(path, **kwargs):
    if not path.exists():
        warnings.warn(f"Missing input: {path}")
        return None
    try:
        return pd.read_csv(path, low_memory=False, **kwargs)
    except Exception as exc:
        warnings.warn(f"Could not read {path.name}: {exc}")
        return None


def load_address_csv(path):
    """Try semicolon first, then comma; reject parses that create one giant column."""
    if not path.exists():
        warnings.warn(f"Missing property-address file: {path}")
        return None
    for sep in (";", ","):
        try:
            frame = pd.read_csv(path, sep=sep, low_memory=False)
            if len(frame.columns) > 1:
                return frame
        except Exception:
            pass
    warnings.warn(f"Could not parse property-address file: {path.name}")
    return None


def parse_rent_value(value):
    """Return numeric part of a rent value such as '1,730' or '970E'; '..' => NaN."""
    if pd.isna(value):
        return np.nan
    raw = str(value).strip()
    if raw in {"", "..", "...", "—", "-"}:
        return np.nan
    cleaned = re.sub(r"[^0-9.\-]", "", raw.replace(",", ""))
    try:
        return float(cleaned) if cleaned else np.nan
    except ValueError:
        return np.nan


def load_benchmark(path):
    """Parse the Statistics Canada export's metadata + multi-row header."""
    if not path.exists():
        warnings.warn(f"Missing benchmark: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except Exception as exc:
        warnings.warn(f"Could not read benchmark {path.name}: {exc}")
        return None

    header_idx = None
    for i, row in enumerate(rows):
        cells = [str(c).strip().lower() for c in row]
        if cells and cells[0] == "geography" and any("q2 2026" in c for c in cells[1:]):
            header_idx = i
            break
    if header_idx is None:
        warnings.warn(f"Could not find Geography/Q2 2026 header in {path.name}")
        return {"table": None}

    # Row after the header contains units (e.g. Dollars); data begins two rows later.
    records = []
    for row in rows[header_idx + 2:]:
        if not row:
            continue
        geography = row[0].strip() if len(row) > 0 else ""
        raw_value = row[1].strip() if len(row) > 1 else ""
        if not geography or geography.lower() in {"geography", "dollars"}:
            continue
        records.append({
            "geography": geography,
            "asking_rent_raw": raw_value,
            "asking_rent_numeric": parse_rent_value(raw_value),
            "is_estimated": "E" in raw_value.upper(),
            "is_unavailable": raw_value in {"", "..", "...", "—", "-"},
        })
    return {"table": pd.DataFrame(records, columns=[
        "geography", "asking_rent_raw", "asking_rent_numeric", "is_estimated", "is_unavailable"
    ])}


def normalize_address(civic, street):
    """Normalize common suffixes without merging different street types."""
    if pd.isna(civic) or pd.isna(street):
        return None
    civic_text = str(civic).strip()
    if civic_text.endswith(".0"):
        civic_text = civic_text[:-2]
    street_text = re.sub(r"[.,]", " ", str(street).upper().strip())
    suffixes = {
        r"\bST\b": "STREET", r"\bAVE\b": "AVENUE", r"\bAV\b": "AVENUE",
        r"\bRD\b": "ROAD", r"\bBLVD\b": "BOULEVARD", r"\bDR\b": "DRIVE",
        r"\bCT\b": "COURT", r"\bHWY\b": "HIGHWAY",
    }
    for pattern, replacement in suffixes.items():
        street_text = re.sub(pattern, replacement, street_text)
    street_text = re.sub(r"\s+", " ", street_text).strip()
    return f"{civic_text} {street_text}"


def save_plot(path):
    plt.tight_layout()
    plt.savefig(path, dpi=160, bbox_inches="tight")
    plt.close()


def main():
    summary_rows = []
    successful_datasets = 0

    # 1. Property-address completeness and canonical-address coverage.
    address_frames = []
    for filename, area in [
        ("property_addresses_downtown.csv", "Downtown"),
        ("property_addresses_west_end.csv", "West End"),
    ]:
        df = load_address_csv(DATA_DIR / filename)
        if df is None:
            summary_rows.append({"dataset": filename, "status": "missing or unreadable"})
            continue
        df["analysis_area"] = area
        if not {"CIVIC_NUMBER", "STD_STREET"}.issubset(df.columns):
            summary_rows.append({
                "dataset": filename, "rows": len(df),
                "status": "loaded but expected CIVIC_NUMBER / STD_STREET columns missing",
                "columns": " | ".join(map(str, df.columns)),
            })
            continue
        df["canonical_address"] = df.apply(
            lambda r: normalize_address(r["CIVIC_NUMBER"], r["STD_STREET"]), axis=1
        )
        address_frames.append(df)
        valid = df["canonical_address"].dropna()
        summary_rows.append({
            "dataset": filename,
            "rows": len(df),
            "missing_civic_number": int(df["CIVIC_NUMBER"].isna().sum()),
            "missing_street": int(df["STD_STREET"].isna().sum()),
            "unique_canonical_addresses": int(valid.nunique()),
            "duplicate_canonical_rows": int(valid.size - valid.nunique()),
            "status": "loaded",
            "note": "Repeated canonical addresses are flagged, not automatically treated as erroneous duplicate properties.",
        })
        successful_datasets += 1

    if address_frames:
        addresses = pd.concat(address_frames, ignore_index=True)
        write_csv(addresses, "property_addresses_normalized.csv")
        area_summary = addresses.groupby("analysis_area").agg(
            rows=("analysis_area", "size"),
            unique_addresses=("canonical_address", "nunique"),
            missing_canonical_address=("canonical_address", lambda s: int(s.isna().sum())),
        ).reset_index()
        write_csv(area_summary, "property_address_area_summary.csv")
        plt.figure(figsize=(8, 5))
        plt.bar(area_summary["analysis_area"], area_summary["unique_addresses"])
        plt.title("Unique canonical property addresses by area")
        plt.ylabel("Unique addresses")
        save_plot(OUTPUT_DIR / "property_addresses_by_area.png")

    # 2. Police crime records. Preserve all rows; create a separate duplicate audit.
    crime_frames = []
    for area, prefix in [("Central Business District", "cbd"), ("West End", "West End")]:
        for year in range(2023, 2027):
            filename = f"{prefix}_{year}.csv"
            path = CRIME_DIR / filename
            df = load_csv(path)
            if df is None:
                summary_rows.append({"dataset": filename, "status": "missing or unreadable"})
                continue
            df["source_area"] = area
            df["source_year"] = year
            df["source_file"] = filename
            crime_frames.append(df)
            successful_datasets += 1

    if crime_frames:
        crime = pd.concat(crime_frames, ignore_index=True, sort=False)
        raw_data_columns = [c for c in crime.columns if c not in {"source_area", "source_year", "source_file", "incident_date"}]

        # Capture true missingness BEFORE creating display-friendly Unknown labels.
        if "TYPE" not in crime.columns:
            crime["TYPE"] = np.nan
        missing_type_before_fill = int(crime["TYPE"].isna().sum())
        if "NEIGHBOURHOOD" not in crime.columns:
            crime["NEIGHBOURHOOD"] = np.nan
        missing_neighbourhood_before_fill = int(crime["NEIGHBOURHOOD"].isna().sum())

        for col in ["YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "X", "Y"]:
            if col in crime.columns:
                crime[col] = pd.to_numeric(crime[col], errors="coerce")
        if {"YEAR", "MONTH", "DAY"}.issubset(crime.columns):
            crime["incident_date"] = pd.to_datetime(
                {"year": crime["YEAR"], "month": crime["MONTH"], "day": crime["DAY"]},
                errors="coerce",
            )
        else:
            crime["incident_date"] = pd.NaT

        # Detect exact repeats using source-record fields; never delete them here.
        duplicate_mask = crime.duplicated(subset=raw_data_columns, keep="first")
        crime["is_exact_duplicate_record"] = duplicate_mask
        duplicate_audit = crime.groupby("source_file", dropna=False).agg(
            rows=("source_file", "size"),
            exact_duplicate_rows=("is_exact_duplicate_record", "sum"),
        ).reset_index()
        if "HUNDRED_BLOCK" in crime.columns:
            privacy_mask = crime["HUNDRED_BLOCK"].astype(str).str.contains(
                "OFFSET TO PROTECT PRIVACY", case=False, na=False
            )
        else:
            privacy_mask = pd.Series(False, index=crime.index)
        crime["is_privacy_protected_location"] = privacy_mask
        privacy_by_file = crime.groupby("source_file")["is_privacy_protected_location"].sum().rename(
            "privacy_protected_location_rows"
        ).reset_index()
        duplicate_audit = duplicate_audit.merge(privacy_by_file, on="source_file", how="left")
        duplicate_audit["missing_type_before_fill"] = crime.groupby("source_file")["TYPE"].apply(
            lambda s: int(s.isna().sum())
        ).values
        write_csv(duplicate_audit, "crime_duplicate_audit_by_file.csv")
        write_csv(crime.loc[duplicate_mask], "crime_exact_duplicate_rows_for_review.csv")

        crime["TYPE"] = crime["TYPE"].fillna("Unknown")
        crime["NEIGHBOURHOOD"] = crime["NEIGHBOURHOOD"].fillna("Unknown")
        write_csv(crime, "police_crime_combined.csv")

        yearly = crime.groupby(["source_area", "source_year"]).size().reset_index(name="incident_records")
        write_csv(yearly, "crime_counts_by_area_year.csv")
        by_type = crime.groupby(["source_area", "TYPE"]).size().reset_index(name="incident_records")
        write_csv(by_type, "crime_counts_by_area_type.csv")
        monthly = crime.dropna(subset=["incident_date"]).groupby(
            [pd.Grouper(key="incident_date", freq="MS"), "source_area"]
        ).size().reset_index(name="incident_records")
        write_csv(monthly, "crime_monthly_trend.csv")

        plt.figure(figsize=(10, 5))
        for area, grp in yearly.groupby("source_area"):
            plt.plot(grp["source_year"], grp["incident_records"], marker="o", label=area)
        plt.title("Police-reported incident records by source area and year")
        plt.xlabel("Source year")
        plt.ylabel("Incident records (not a population-adjusted rate)")
        plt.legend()
        save_plot(OUTPUT_DIR / "crime_counts_by_year.png")

        top_types = by_type.groupby("TYPE")["incident_records"].sum().sort_values(ascending=False).head(10).sort_values()
        plt.figure(figsize=(9, 6))
        plt.barh(top_types.index.astype(str), top_types.values)
        plt.title("Most frequently recorded crime categories in supplied files")
        plt.xlabel("Incident records")
        save_plot(OUTPUT_DIR / "top_crime_categories.png")

        zero_coordinate_rows = 0
        if {"X", "Y"}.issubset(crime.columns):
            zero_coordinate_rows = int(((crime["X"] == 0) & (crime["Y"] == 0)).sum())
        summary_rows.append({
            "dataset": "combined_police_crime",
            "rows": len(crime),
            "exact_duplicate_rows_flagged": int(duplicate_mask.sum()),
            "privacy_protected_location_rows": int(privacy_mask.sum()),
            "zero_coordinate_rows": zero_coordinate_rows,
            "missing_type_before_fill": missing_type_before_fill,
            "missing_neighbourhood_before_fill": missing_neighbourhood_before_fill,
            "date_min": str(crime["incident_date"].min()),
            "date_max": str(crime["incident_date"].max()),
            "status": "loaded; duplicates preserved and audit exported",
            "note": "Do not automatically delete repeated rows. Privacy-protected locations are not geocodable. Counts are not population-adjusted rates; 2026 may be partial.",
        })

    # 3. Q2 2026 average ASKING rent benchmarks.
    benchmark_inputs = [
        ("rent_benchmark_1br_q2_2026.csv", "1 bedroom"),
        ("rent_benchmark_2br_q2_2026.csv", "2 bedrooms"),
        ("rent_benchmark_3br_q2_2026.csv", "3+ bedrooms"),
    ]
    benchmark_rows = []
    for filename, bedroom_label in benchmark_inputs:
        parsed = load_benchmark(DATA_DIR / filename)
        if parsed is None:
            summary_rows.append({"dataset": filename, "status": "missing or unreadable"})
            continue
        table = parsed.get("table")
        if table is None or "geography" not in table.columns:
            summary_rows.append({"dataset": filename, "status": "could not identify benchmark table header"})
            continue
        vancouver = table[table["geography"].astype(str).str.contains("Vancouver", case=False, na=False)].copy()
        for _, row in vancouver.iterrows():
            benchmark_rows.append({
                "file": filename,
                "bedroom_category": bedroom_label,
                "geography": row["geography"],
                "asking_rent_raw": row["asking_rent_raw"],
                "asking_rent_numeric": row["asking_rent_numeric"],
                "is_estimated": row["is_estimated"],
                "is_unavailable": row["is_unavailable"],
            })
        summary_rows.append({
            "dataset": filename, "rows_parsed": len(table), "vancouver_rows_found": len(vancouver),
            "status": "loaded" if len(vancouver) else "loaded but no Vancouver geography row found",
        })
        if len(vancouver):
            successful_datasets += 1

    if benchmark_rows:
        benchmarks = pd.DataFrame(benchmark_rows)
        write_csv(benchmarks, "rent_benchmarks_extracted.csv")
        usable = benchmarks.dropna(subset=["asking_rent_numeric"])
        if not usable.empty:
            plt.figure(figsize=(9, 5))
            labels = usable["bedroom_category"] + " — " + usable["geography"].astype(str)
            plt.bar(labels, usable["asking_rent_numeric"])
            plt.title("Extracted Vancouver average asking-rent benchmarks")
            plt.ylabel("Average asking rent (CAD)")
            plt.xticks(rotation=20, ha="right")
            save_plot(OUTPUT_DIR / "rent_benchmarks.png")
    else:
        summary_rows.append({"dataset": "rent_benchmarks_extracted.csv", "status": "no benchmark rows extracted"})

    # 4. CAFC fraud data: profile schema; do not infer address-level rental fraud.
    fraud_path = DATA_DIR / "fraud" / "cafc_fraud_reporting.csv"
    fraud = load_csv(fraud_path)
    if fraud is not None:
        profile = pd.DataFrame({
            "column": fraud.columns,
            "dtype": [str(fraud[c].dtype) for c in fraud.columns],
            "missing_count": [int(fraud[c].isna().sum()) for c in fraud.columns],
            "missing_percent": [round(float(fraud[c].isna().mean() * 100), 2) for c in fraud.columns],
            "unique_values": [int(fraud[c].nunique(dropna=True)) for c in fraud.columns],
        })
        write_csv(profile, "cafc_fraud_data_profile.csv")
        write_csv(fraud.head(100), "cafc_fraud_sample_first_100_rows.csv")
        summary_rows.append({
            "dataset": "cafc_fraud_reporting.csv", "rows": len(fraud), "columns": len(fraud.columns),
            "status": "loaded; schema/definitions need review",
            "note": "General or national fraud reports do not establish rental-deposit fraud at a specific address.",
        })
        successful_datasets += 1
    else:
        summary_rows.append({"dataset": "cafc_fraud_reporting.csv", "status": "missing or unreadable (optional input)"})

    summary = pd.DataFrame(summary_rows)
    write_csv(summary, "eda_summary.csv")
    (OUTPUT_DIR / "EDA_README.txt").write_text(
        "RentalShield EDA outputs\n"
        "========================\n"
        "Start with eda_summary.csv.\n"
        "crime_duplicate_audit_by_file.csv flags suspected exact repeats by source file; no crime records are automatically removed.\n"
        "crime_exact_duplicate_rows_for_review.csv contains repeated records after the first occurrence for manual review.\n"
        "Police incident counts are not population-adjusted crime rates. 2026 may be a partial year.\n"
        "Privacy-protected locations cannot support address-level matching.\n"
        "Rent benchmarks are average asking rents; check raw estimate/unavailable markers.\n"
        "CAFC general fraud statistics do not prove rental-deposit fraud at an individual address.\n"
        "These exploratory results do not prove a property is safe or fraudulent.\n",
        encoding="utf-8",
    )
    print(f"EDA complete. Outputs: {OUTPUT_DIR}")
    print(f"Summary rows (dataset checks): {len(summary)}")
    print(f"Successfully loaded data files/sets: {successful_datasets}")
    print(f"Summary file: {OUTPUT_DIR / 'eda_summary.csv'}")
    if "combined_police_crime" in summary.get("dataset", pd.Series(dtype=str)).values:
        row = summary.loc[summary["dataset"] == "combined_police_crime"].iloc[0]
        print(f"Crime records preserved: {int(row['rows'])}; duplicate rows flagged for review: {int(row['exact_duplicate_rows_flagged'])}")


if __name__ == "__main__":
    main()
