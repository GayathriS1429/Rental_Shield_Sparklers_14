"""Nearby police-reported incident context and Vancouver comparison for RentalShield.

Uses the original VPD GeoDASH combined incident export and City property-address
points. Counts are approximate reported incident records within a radius; they
are not a population-adjusted crime rate or a safety/fraud determination.
"""
from __future__ import annotations

from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
CITYWIDE_DIR = DATA_DIR / "police_crime" / "citywide_reference"
CITYWIDE_CRIME_FILE = CITYWIDE_DIR / "crimedata_csv_AllNeighbourhoods_AllYears.csv"
CITYWIDE_ADDRESS_FILE = CITYWIDE_DIR / "vancouver_property_addresses.csv"
PROCESSED_DIR = CITYWIDE_DIR / "processed"

_WGS84_TO_UTM10 = Transformer.from_crs(
    "EPSG:4326", "EPSG:26910", always_xy=True
)
_DEFAULT_YEARS = (2023, 2024, 2025, 2026)
_ALLOWED_YEARS = {2023, 2024, 2025, 2026}
_ALLOWED_AREAS = {
    "downtown": "Downtown / Central Business District",
    "cbd": "Downtown / Central Business District",
    "central business district": "Downtown / Central Business District",
    "downtown / central business district": "Downtown / Central Business District",
    "west end": "West End",
    "west_end": "West End",
}
_PRIMARY_RADIUS = 500
_MIN_AREA_REFERENCE_POINTS = 50


def _normalize_area(area: str) -> str:
    if not isinstance(area, str) or not area.strip():
        raise ValueError("Area must be 'downtown' or 'west end'.")
    key = area.strip().lower().replace("_", " ")
    if key not in _ALLOWED_AREAS:
        raise ValueError("Area must be 'downtown' or 'west end'.")
    return _ALLOWED_AREAS[key]


def _area_key(value) -> str:
    """Normalize source neighbourhood names for comparisons."""
    text = str(value or "").strip()
    key = text.casefold().replace("_", " ")
    aliases = {
        "central business district": "downtown",
        "downtown / central business district": "downtown",
        "downtown": "downtown",
        "west end": "west end",
    }
    return aliases.get(key, key)


def _display_area(value) -> str:
    key = _area_key(value)
    if key == "downtown":
        return "Downtown"
    if key == "west end":
        return "West End"
    return str(value).strip()


def _validate_target_coordinates(latitude: float, longitude: float):
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("Latitude and longitude must be numeric.") from exc
    if (
        not isfinite(lat)
        or not isfinite(lon)
        or not -90 <= lat <= 90
        or not -180 <= lon <= 180
    ):
        raise ValueError("Provide valid latitude and longitude values.")
    x, y = _WGS84_TO_UTM10.transform(lon, lat)
    return lat, lon, float(x), float(y)


def _normalize_years(years: int | Iterable[int] | None) -> list[int]:
    if years is None:
        selected = list(_DEFAULT_YEARS)
    elif isinstance(years, int):
        selected = [years]
    else:
        selected = [int(year) for year in years]
    selected = sorted(set(selected))
    if not selected:
        raise ValueError("Select at least one crime data year.")
    invalid = sorted(set(selected) - _ALLOWED_YEARS)
    if invalid:
        raise ValueError(
            "Available comparison years are 2023, 2024, 2025 and 2026. "
            f"Unsupported year(s): {invalid}"
        )
    return selected


@lru_cache(maxsize=1)
def _load_all_citywide_crime() -> pd.DataFrame:
    """Load/cache only required columns from the original combined export."""
    if not CITYWIDE_CRIME_FILE.is_file():
        raise FileNotFoundError(
            "Citywide VPD crime CSV not found. Expected: "
            f"{CITYWIDE_CRIME_FILE}"
        )
    required = {"TYPE", "YEAR", "X", "Y", "NEIGHBOURHOOD"}
    df = pd.read_csv(
        CITYWIDE_CRIME_FILE,
        usecols=lambda col: col.strip() in required,
        encoding="utf-8-sig",
        low_memory=False,
    )
    df.columns = df.columns.str.strip()
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Citywide crime CSV is missing required columns: {sorted(missing)}"
        )
    df["YEAR"] = pd.to_numeric(df["YEAR"], errors="coerce")
    df["X"] = pd.to_numeric(df["X"], errors="coerce")
    df["Y"] = pd.to_numeric(df["Y"], errors="coerce")
    df["TYPE"] = df["TYPE"].fillna("Unknown").astype(str).str.strip()
    df["NEIGHBOURHOOD"] = (
        df["NEIGHBOURHOOD"].fillna("Unknown").astype(str).str.strip()
    )
    df = df[
        df["YEAR"].notna()
        & df["X"].notna()
        & df["Y"].notna()
        & np.isfinite(df["X"])
        & np.isfinite(df["Y"])
    ].copy()
    df["YEAR"] = df["YEAR"].astype(int)
    return df


@lru_cache(maxsize=4)
def _load_year_tree(year: int):
    """Prepare one cached spatial index per year to make report checks quicker."""
    df = _load_all_citywide_crime()
    selected = df.loc[df["YEAR"].eq(year)].reset_index(drop=True).copy()
    if selected.empty:
        raise ValueError(f"The citywide incident file contains no records for {year}.")
    points = selected[["X", "Y"]].to_numpy(dtype=np.float64)
    return selected, cKDTree(points)


@lru_cache(maxsize=8)
def _load_combined_reference_counts(years_tuple: tuple[int, ...]) -> pd.DataFrame:
    """Join the 500 m reference-point counts across the selected years."""
    combined = None
    for year in years_tuple:
        path = PROCESSED_DIR / f"crime_reference_counts_{_PRIMARY_RADIUS}m_{year}.csv"
        if not path.is_file():
            raise FileNotFoundError(
                "The Vancouver comparison baseline is missing. Run "
                "`python build_citywide_crime_baseline.py` from your project root "
                "after replacing it with the supplied updated version. "
                f"Missing baseline: {path}"
            )
        frame = pd.read_csv(path, low_memory=False)
        required = {
            "reference_point_id", "Geo Local Area", "x_m", "y_m", "incident_count"
        }
        if not required.issubset(frame.columns):
            raise ValueError(
                "Your baseline files use the old format and do not include "
                "neighbourhood labels and stable reference-point IDs. Rerun "
                "`python build_citywide_crime_baseline.py` to rebuild them."
            )
        frame = frame[
            ["reference_point_id", "Geo Local Area", "x_m", "y_m", "incident_count"]
        ].copy()
        frame["incident_count"] = pd.to_numeric(
            frame["incident_count"], errors="coerce"
        ).fillna(0)
        frame["x_m"] = pd.to_numeric(frame["x_m"], errors="coerce")
        frame["y_m"] = pd.to_numeric(frame["y_m"], errors="coerce")
        frame["area_key"] = frame["Geo Local Area"].map(_area_key)
        frame = frame.rename(columns={"incident_count": f"count_{year}"})

        if combined is None:
            combined = frame
        else:
            counts = [column for column in frame.columns if column.startswith("count_")]
            frame = frame[
                ["reference_point_id", *counts]
            ]
            combined = combined.merge(
                frame, on="reference_point_id", how="inner", validate="one_to_one"
            )

    if combined is None or combined.empty:
        raise ValueError("The combined Vancouver comparison baseline is empty.")

    count_cols = [f"count_{year}" for year in years_tuple]
    combined["combined_incident_count"] = combined[count_cols].sum(axis=1)
    return combined


def _classify_percentile(percentile: float) -> str:
    # RentalShield-defined bands, not official police categories.
    if percentile <= (100.0 / 3.0):
        return "Low"
    if percentile <= (200.0 / 3.0):
        return "Medium"
    return "High"


def _format_pct(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _comparison_summary(local_count: int, reference_counts: np.ndarray) -> dict:
    values = np.asarray(reference_counts, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("No valid Vancouver comparison counts are available.")
    less = int(np.count_nonzero(values < local_count))
    equal = int(np.count_nonzero(values == local_count))
    percentile = ((less + 0.5 * equal) / values.size) * 100.0
    return {
        "comparison_percentile": round(float(percentile), 1),
        "relative_activity_level": _classify_percentile(float(percentile)),
        "citywide_median_comparable_count": float(np.median(values)),
        "comparison_locations_count": int(values.size),
    }


def check_nearby_crime(
    latitude: float,
    longitude: float,
    area: str,
    years: int | Iterable[int] | None = None,
    radii_meters: Iterable[int] = (250, 500),
    top_n_types: int = 5,
) -> dict:
    """Return yearly diagnostic data plus a single combined 2023–2026 summary.

    The user-facing summary contains exactly three concise lines. The main
    relative percentage is the combined-period percentile, not an annual rate.
    """
    lat, lon, property_x, property_y = _validate_target_coordinates(latitude, longitude)
    area_label = _normalize_area(area)
    selected_years = _normalize_years(years)

    try:
        radii = sorted({int(radius) for radius in radii_meters})
    except (TypeError, ValueError) as exc:
        raise ValueError("Radii must be positive numbers of metres.") from exc
    if not radii or any(radius <= 0 for radius in radii):
        raise ValueError("Provide at least one positive radius in metres.")
    if not isinstance(top_n_types, int) or top_n_types < 1:
        raise ValueError("top_n_types must be a positive integer.")

    # Compute local count and incident types in each selected year. These
    # yearly values are retained for traceability but the UI displays only
    # the combined-period three-line summary.
    yearly_summaries = {}
    combined_type_counts = {}
    aggregate_counts_by_radius = {
        str(radius): {
            "radius_meters": radius,
            "reported_incident_records": 0,
            "records_by_year": {},
            "top_incident_types": [],
        }
        for radius in radii
    }

    for year in selected_years:
        incidents, tree = _load_year_tree(int(year))
        max_radius = max(radii)
        indices = tree.query_ball_point([property_x, property_y], r=max_radius)
        nearby_max = incidents.iloc[indices].copy() if indices else incidents.iloc[0:0].copy()
        if not nearby_max.empty:
            dx = nearby_max["X"].to_numpy(dtype=np.float64) - property_x
            dy = nearby_max["Y"].to_numpy(dtype=np.float64) - property_y
            nearby_max["DISTANCE_METERS"] = np.hypot(dx, dy)
        else:
            nearby_max["DISTANCE_METERS"] = pd.Series(dtype=float)

        year_radius_data = {}
        for radius in radii:
            nearby = (
                nearby_max.loc[nearby_max["DISTANCE_METERS"] <= radius]
                if not nearby_max.empty else nearby_max
            )
            type_counts = nearby["TYPE"].value_counts()
            count = int(len(nearby))
            top = [
                {"type": str(kind), "count": int(n)}
                for kind, n in type_counts.head(top_n_types).items()
            ]
            year_radius_data[str(radius)] = {
                "radius_meters": radius,
                "reported_incident_records": count,
                "top_incident_types": top,
            }
            agg = aggregate_counts_by_radius[str(radius)]
            agg["reported_incident_records"] += count
            agg["records_by_year"][str(year)] = count
            for kind, n in type_counts.items():
                counts = combined_type_counts.setdefault(str(radius), {})
                counts[str(kind)] = counts.get(str(kind), 0) + int(n)

        primary_data = year_radius_data.get(str(_PRIMARY_RADIUS))
        if primary_data is None:
            # Always summarize the standard 500 m radius for the classification.
            indices_500 = tree.query_ball_point([property_x, property_y], r=_PRIMARY_RADIUS)
            near_500 = incidents.iloc[indices_500].copy() if indices_500 else incidents.iloc[0:0].copy()
            type_counts = near_500["TYPE"].value_counts()
            primary_data = {
                "radius_meters": _PRIMARY_RADIUS,
                "reported_incident_records": int(len(near_500)),
                "top_incident_types": [
                    {"type": str(kind), "count": int(n)}
                    for kind, n in type_counts.head(top_n_types).items()
                ],
            }

        yearly_summaries[str(year)] = {
            "year": year,
            "timeframe_label": "Year to date (partial year)" if year == 2026 else "Full calendar year",
            "is_partial_year": year == 2026,
            "radius_meters": _PRIMARY_RADIUS,
            "reported_incident_records": primary_data["reported_incident_records"],
            "top_incident_types": primary_data["top_incident_types"],
            "top_incident_type_names": [
                item["type"] for item in primary_data["top_incident_types"][:3]
            ],
        }

    baseline = _load_combined_reference_counts(tuple(selected_years))
    reference_counts = baseline["combined_incident_count"].to_numpy(dtype=np.float64)
    local_primary_count = int(aggregate_counts_by_radius.get(str(_PRIMARY_RADIUS), {}).get(
        "reported_incident_records", 0
    ))
    comparison = _comparison_summary(local_primary_count, reference_counts)
    top_types = sorted(
        combined_type_counts.get(str(_PRIMARY_RADIUS), {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:3]
    top_type_names = [kind for kind, _ in top_types]
    period = f"{min(selected_years)}–{max(selected_years)}"
    timeframe = period
    if 2026 in selected_years:
        timeframe += " (2026 year-to-date)"

    if top_type_names:
        type_line = (
            f"Common reported incident types within approximately 500 m, "
            f"{period}: {', '.join(top_type_names)}."
        )
    else:
        type_line = (
            f"No located incident records were found within approximately 500 m "
            f"during {period} in the supplied dataset."
        )

    percentile = float(comparison["comparison_percentile"])
    level = comparison["relative_activity_level"]
    if level == "High":
        compare_line = (
            f"Reported incident activity was higher than approximately "
            f"{_format_pct(percentile)}% of comparable Vancouver 500 m address areas."
        )
    elif level == "Low":
        compare_line = (
            f"Reported incident activity was lower than counts in approximately "
            f"{_format_pct(max(0.0, 100.0 - percentile))}% of comparable Vancouver 500 m address areas."
        )
    else:
        compare_line = (
            f"Reported incident activity was around the middle of the range among "
            f"comparable Vancouver 500 m address areas (about the {_format_pct(percentile)}th percentile)."
        )
    compare_line += f" Relative activity: {level}."

    context_line = (
        "These are approximate police-reported incident records, not an official "
        "crime rate or a safety rating; incident locations may be generalized."
    )
    if 2026 in selected_years:
        context_line = (
            "2026 data is year-to-date. Records are approximate, not an official "
            "crime rate or a safety rating; locations may be generalized."
        )

    aggregate_summary = {
        "timeframe_label": timeframe,
        "years_included": selected_years,
        "radius_meters": _PRIMARY_RADIUS,
        "reported_incident_records": local_primary_count,
        "top_incident_types": [
            {"type": kind, "count": count} for kind, count in top_types
        ],
        "top_incident_type_names": top_type_names,
        "comparison_percentile": percentile,
        "relative_activity_level": level,
        "citywide_median_comparable_count": comparison["citywide_median_comparable_count"],
        "comparison_locations_count": comparison["comparison_locations_count"],
        "summary_lines": [type_line, compare_line, context_line],
        "summary_sentence": " ".join([type_line, compare_line, context_line]),
    }

    # The radius_results structure is retained for JSON/API compatibility.
    for radius_key, record in aggregate_counts_by_radius.items():
        type_items = sorted(
            combined_type_counts.get(radius_key, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[:top_n_types]
        record["top_incident_types"] = [
            {"type": kind, "count": count} for kind, count in type_items
        ]

    return {
        "status": "available",
        "message": "Combined-period nearby crime context was generated.",
        "area": area_label,
        "property_coordinates": {"latitude": lat, "longitude": lon},
        "years_included": selected_years,
        "timeframe_label": timeframe,
        "primary_radius_meters": _PRIMARY_RADIUS,
        "aggregate_summary": aggregate_summary,
        "summary_lines": aggregate_summary["summary_lines"],
        "summary_sentence": aggregate_summary["summary_sentence"],
        "relative_activity_level": level,
        "comparison_percentile": percentile,
        "yearly_summaries": yearly_summaries,
        "radius_results": aggregate_counts_by_radius,
        "comparison_method": {
            "reference_points": "Unique City property-address coordinate points, compared using 500 m buffers.",
            "radius_meters": _PRIMARY_RADIUS,
            "classification": "RentalShield-defined relative tiers: bottom third Low, middle third Medium, top third High.",
            "not_an_official_crime_rate": True,
        },
        "interpretation_notes": [
            "The combined percentage is a percentile comparison against sampled Vancouver 500 m address areas, not a percentage of all city crimes.",
            "Property-address records indicate locations in the address dataset, not available rental units.",
            "Police-reported incident coordinates can be generalized or offset for privacy.",
        ],
    }


def suggest_alternative_areas(
    latitude: float,
    longitude: float,
    current_neighbourhood: str | None,
    monthly_rent: float | None,
    bedrooms: int | None = None,
    limit: int = 3,
    years: int | Iterable[int] | None = None,
) -> dict:
    """Suggest nearby neighbourhoods with lower median relative incident counts.

    Suggestions name neighbourhoods, not specific properties. Monthly rent is
    shown only as the user's own target; supplied datasets do not establish
    neighbourhood-specific rent averages or current unit availability.
    """
    lat, lon, target_x, target_y = _validate_target_coordinates(latitude, longitude)
    selected_years = _normalize_years(years)
    try:
        requested_limit = max(1, min(int(limit), 3))
    except (TypeError, ValueError):
        requested_limit = 3

    reference = _load_combined_reference_counts(tuple(selected_years)).copy()
    reference = reference[
        reference["area_key"].notna()
        & reference["area_key"].ne("")
        & reference["area_key"].ne("unknown")
    ].copy()
    if reference.empty:
        return {
            "status": "unavailable",
            "message": "Neighbourhood reference records were not available.",
            "areas": [],
        }

    # Summarize the median number of 500 m incident records around sampled
    # property-address points within each named neighbourhood.
    stats = (
        reference.groupby("area_key", dropna=False)
        .agg(
            display_area=("Geo Local Area", lambda values: _display_area(values.mode().iloc[0] if not values.mode().empty else values.iloc[0])),
            address_points_in_dataset=("reference_point_id", "nunique"),
            median_incident_records=("combined_incident_count", "median"),
            centroid_x=("x_m", "mean"),
            centroid_y=("y_m", "mean"),
        )
        .reset_index()
    )
    stats = stats[
        stats["address_points_in_dataset"] >= _MIN_AREA_REFERENCE_POINTS
    ].copy()
    if stats.empty:
        return {"status": "unavailable", "message": "No neighbourhoods had enough reference points.", "areas": []}

    area_values = stats["median_incident_records"].to_numpy(dtype=np.float64)
    area_percentiles = []
    for value in area_values:
        less = int(np.count_nonzero(area_values < value))
        equal = int(np.count_nonzero(area_values == value))
        area_percentiles.append(((less + 0.5 * equal) / len(area_values)) * 100.0)
    stats["relative_percentile"] = area_percentiles
    stats["relative_activity_level"] = stats["relative_percentile"].map(_classify_percentile)

    current_key = _area_key(current_neighbourhood) if current_neighbourhood else ""
    current_match = stats.loc[stats["area_key"].eq(current_key)]
    if current_match.empty:
        # If label mapping does not find the target neighbourhood, use the
        # nearest sampled neighbourhood as the comparison anchor.
        dx_all = stats["centroid_x"].to_numpy(dtype=float) - target_x
        dy_all = stats["centroid_y"].to_numpy(dtype=float) - target_y
        nearest_idx = int(np.argmin(np.hypot(dx_all, dy_all)))
        current_match = stats.iloc[[nearest_idx]]
        current_key = str(current_match.iloc[0]["area_key"])

    current_median = float(current_match.iloc[0]["median_incident_records"])
    current_level = str(current_match.iloc[0]["relative_activity_level"])

    dx = stats["centroid_x"].to_numpy(dtype=float) - target_x
    dy = stats["centroid_y"].to_numpy(dtype=float) - target_y
    stats["distance_km"] = np.hypot(dx, dy) / 1000.0

    # Candidates should be close to the entered address and have a lower
    # neighbourhood median than the current neighbourhood's median.
    candidates = stats[
        ~stats["area_key"].eq(current_key)
        & stats["median_incident_records"].lt(current_median)
        & stats["distance_km"].between(0.1, 12.0, inclusive="both")
    ].copy()

    if candidates.empty:
        return {
            "status": "no_matching_areas",
            "message": (
                "No nearby neighbourhood met the lower-relative-activity "
                "criteria using the available comparison data."
            ),
            "areas": [],
            "target_monthly_rent_cad": (
                round(float(monthly_rent), 2)
                if monthly_rent is not None else None
            ),
            "current_neighbourhood_level": current_level,
        }

    candidates = candidates.sort_values(
        ["distance_km", "median_incident_records", "display_area"],
        ascending=[True, True, True],
    ).head(requested_limit)

    target_rent = None
    try:
        if monthly_rent is not None and float(monthly_rent) > 0:
            target_rent = round(float(monthly_rent), 2)
    except (TypeError, ValueError):
        target_rent = None

    bedroom_label = None
    if bedrooms in (1, 2, 3):
        bedroom_label = "3+ bedrooms" if bedrooms == 3 else f"{bedrooms} bedroom(s)"

    output_areas = []
    for _, row in candidates.iterrows():
        if target_rent is not None:
            rent_note = (
                f"Use around CAD ${target_rent:,.0f}/month as your search target "
                "(the rent you entered); current availability and neighbourhood-specific "
                "rent averages are not verified by the supplied datasets."
            )
        else:
            rent_note = (
                "Monthly rent was not supplied, so a price target could not be included."
            )
        if bedroom_label:
            rent_note += f" Check current listings for {bedroom_label}."

        output_areas.append({
            "area_name": str(row["display_area"]),
            "distance_from_listing_km": round(float(row["distance_km"]), 1),
            "relative_activity_level": str(row["relative_activity_level"]),
            "median_nearby_incident_records": round(float(row["median_incident_records"]), 1),
            "address_points_in_dataset": int(row["address_points_in_dataset"]),
            "target_monthly_rent_cad": target_rent,
            "rent_note": rent_note,
            "message": (
                f"{row['display_area']} has a lower median count of reported "
                f"incidents around sampled 500 m address points than {current_neighbourhood or 'the current area'} "
                f"for {selected_years[0]}–{selected_years[-1]}."
            ),
        })

    return {
        "status": "available" if output_areas else "no_matching_areas",
        "message": (
            "These are neighbourhoods to explore, not confirmed available rental properties."
            if output_areas else "No alternative areas met the comparison rules."
        ),
        "current_neighbourhood": current_neighbourhood,
        "current_neighbourhood_median_incident_records": current_median,
        "current_neighbourhood_level": current_level,
        "timeframe_label": (
            f"{selected_years[0]}–{selected_years[-1]} "
            + ("(2026 year-to-date)" if 2026 in selected_years else "")
        ),
        "radius_meters": _PRIMARY_RADIUS,
        "target_monthly_rent_cad": target_rent,
        "bedrooms": bedrooms,
        "areas": output_areas,
        "limitations": [
            "Property-address data confirms sampled address points, not available rentals.",
            "The entered rent is a search target, not a measured average rent for the suggested neighbourhood.",
            "Lower neighbourhood-level incident counts do not guarantee safety.",
        ],
    }
