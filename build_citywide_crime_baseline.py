"""Build Vancouver-wide, 500 m crime comparison baselines for RentalShield.

Place this script next to app.py and run:
    python build_citywide_crime_baseline.py

Required source files under data/police_crime/citywide_reference/:
  - crimedata_csv_AllNeighbourhoods_AllYears.csv
  - vancouver_property_addresses.csv

Outputs are cached reference counts for 2023–2026. 2026 is partial-year.
Counts represent police-reported incident records, not population-adjusted rates.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

PROJECT_DIR = Path(__file__).resolve().parent
CITYWIDE_DIR = PROJECT_DIR / "data" / "police_crime" / "citywide_reference"
CRIME_FILE = CITYWIDE_DIR / "crimedata_csv_AllNeighbourhoods_AllYears.csv"
ADDRESS_FILE = CITYWIDE_DIR / "vancouver_property_addresses.csv"
OUTPUT_DIR = CITYWIDE_DIR / "processed"
YEARS = [2023, 2024, 2025, 2026]
RADIUS_METERS = 500

_WGS84_TO_UTM10 = Transformer.from_crs(
    "EPSG:4326", "EPSG:26910", always_xy=True
)


def load_reference_points() -> pd.DataFrame:
    """Load unique approximately 1 m property-address points and neighbourhood names."""
    if not ADDRESS_FILE.is_file():
        raise FileNotFoundError(
            f"Property-address CSV not found: {ADDRESS_FILE}. "
            "Check its exact filename in citywide_reference."
        )

    df = pd.read_csv(
        ADDRESS_FILE,
        sep=None,
        engine="python",
        usecols=["geo_point_2d", "Geo Local Area"],
        dtype=str,
        encoding="utf-8-sig",
    )
    parts = df["geo_point_2d"].str.extract(
        r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
    )
    lat = pd.to_numeric(parts[0], errors="coerce")
    lon = pd.to_numeric(parts[1], errors="coerce")
    valid = lat.between(-90, 90) & lon.between(-180, 180)
    df = df.loc[valid].copy()
    lat = lat.loc[valid].to_numpy(dtype=np.float64)
    lon = lon.loc[valid].to_numpy(dtype=np.float64)

    x, y = _WGS84_TO_UTM10.transform(lon, lat)
    df["x_m"] = np.rint(x).astype(np.int64)
    df["y_m"] = np.rint(y).astype(np.int64)
    df["Geo Local Area"] = (
        df["Geo Local Area"].fillna("Unknown").astype(str).str.strip()
    )
    df.loc[df["Geo Local Area"].eq(""), "Geo Local Area"] = "Unknown"

    # Preserve one reference sample per approximate 1 m coordinate cell.
    df = df.drop_duplicates(["x_m", "y_m"]).copy()
    df["reference_point_id"] = (
        df["x_m"].astype(str) + "_" + df["y_m"].astype(str)
    )
    df = df[
        ["reference_point_id", "Geo Local Area", "x_m", "y_m"]
    ].reset_index(drop=True)

    if len(df) < 1000:
        raise ValueError(
            f"Only {len(df)} usable unique property coordinates were found; "
            "check geo_point_2d in the City property-address dataset."
        )

    print(f"Usable property-address reference points: {len(df):,}")
    print(f"Neighbourhoods represented: {df['Geo Local Area'].nunique():,}")
    return df


def load_incidents_for_year(year: int) -> np.ndarray:
    """Return incident X/Y coordinates for one year."""
    if not CRIME_FILE.is_file():
        raise FileNotFoundError(f"Citywide crime CSV not found: {CRIME_FILE}")

    df = pd.read_csv(
        CRIME_FILE,
        usecols=["YEAR", "X", "Y"],
        dtype={"YEAR": "string", "X": "string", "Y": "string"},
        encoding="utf-8-sig",
        low_memory=False,
    )
    year_values = pd.to_numeric(df["YEAR"], errors="coerce")
    selected = df.loc[year_values.eq(year), ["X", "Y"]].copy()
    selected["X"] = pd.to_numeric(selected["X"], errors="coerce")
    selected["Y"] = pd.to_numeric(selected["Y"], errors="coerce")
    selected = selected[
        selected["X"].notna()
        & selected["Y"].notna()
        & np.isfinite(selected["X"])
        & np.isfinite(selected["Y"])
    ]
    points = selected[["X", "Y"]].to_numpy(dtype=np.float64)
    if len(points) == 0:
        raise ValueError(f"No usable crime-coordinate records found for {year}.")
    print(f"{year}: incident coordinate records loaded: {len(points):,}")
    return points


def query_counts(tree: cKDTree, points: np.ndarray, radius: int) -> np.ndarray:
    """Get nearby incident counts, with compatibility for older SciPy releases."""
    try:
        return np.asarray(
            tree.query_ball_point(points, r=radius, return_length=True),
            dtype=np.int32,
        )
    except TypeError:
        output = np.empty(len(points), dtype=np.int32)
        chunk_size = 5000
        for start in range(0, len(points), chunk_size):
            chunk = points[start:start + chunk_size]
            matches = tree.query_ball_point(chunk, r=radius)
            output[start:start + len(chunk)] = [
                len(items) for items in matches
            ]
        return output


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    reference_df = load_reference_points()
    reference_xy = reference_df[["x_m", "y_m"]].to_numpy(dtype=np.float64)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_crime_file": CRIME_FILE.name,
        "source_address_file": ADDRESS_FILE.name,
        "radius_meters": RADIUS_METERS,
        "coordinate_system": "EPSG:26910 (UTM Zone 10N, NAD83)",
        "address_reference_points": int(len(reference_df)),
        "neighbourhoods": sorted(reference_df["Geo Local Area"].unique().tolist()),
        "years": {},
        "interpretation": (
            "Counts around City property-address points are a relative incident-activity "
            "baseline, not a population-adjusted crime rate or official safety rating."
        ),
    }

    for year in YEARS:
        print(f"\nBuilding {year} comparison baseline ({RADIUS_METERS} m)...")
        crime_points = load_incidents_for_year(year)
        tree = cKDTree(crime_points)
        counts = query_counts(tree, reference_xy, RADIUS_METERS)

        output = OUTPUT_DIR / f"crime_reference_counts_{RADIUS_METERS}m_{year}.csv"
        saved = reference_df.copy()
        saved["incident_count"] = counts
        saved.to_csv(output, index=False)

        metadata["years"][str(year)] = {
            "incident_records_with_coordinates": int(len(crime_points)),
            "reference_points_scored": int(len(counts)),
            "median_incident_count": float(np.median(counts)),
            "p33_incident_count": float(np.quantile(counts, 1 / 3)),
            "p67_incident_count": float(np.quantile(counts, 2 / 3)),
            "output_file": output.name,
        }
        print(f"Saved: {output}")
        print(f"Median records within 500 m: {np.median(counts):.1f}")

    metadata_path = OUTPUT_DIR / "crime_reference_baseline_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"\nDone. Metadata: {metadata_path}")
    print("Baseline files now include stable point IDs, coordinates and neighbourhoods.")
    print("Next: create a fresh rental report to use the combined 2023–2026 comparison.")


if __name__ == "__main__":
    main()
