
from pathlib import Path

import pandas as pd


# Project data directory
DATA_DIR = Path(__file__).resolve().parent / "data"


def load_csv(filename, separator=","):
    """Load a CSV file as strings without modifying the source file."""
    path = DATA_DIR / filename

    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")

    df = pd.read_csv(
        path,
        sep=separator,
        dtype=str,
        encoding="utf-8-sig",
    )

    df.columns = df.columns.str.strip()
    return df


def load_property_addresses(area):
    """Load property address records for Downtown or West End."""
    area = area.strip().lower().replace(" ", "_")

    if area not in {"downtown", "west_end"}:
        raise ValueError("Area must be 'downtown' or 'west end'.")

    df = load_csv(
        f"property_addresses_{area}.csv",
        separator=";",
    )

    required = {
        "CIVIC_NUMBER",
        "STD_STREET",
        "Geo Local Area",
    }
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing address columns: {sorted(missing)}"
        )

    return df


def load_rental_standards(area):
    """Load published rental-standard issue records for an area."""
    area = area.strip().lower().replace(" ", "_")

    if area not in {"downtown", "west_end"}:
        raise ValueError("Area must be 'downtown' or 'west end'.")

    # These files use semicolons, not commas.
    df = load_csv(
        f"rental_standards_{area}.csv",
        separator=";",
    )

    required = {
        "StreetNumber",
        "Street",
        "TOTALOUTSTANDING",
        "TotalUnits",
    }
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing rental-standard columns: {sorted(missing)}"
        )

    for column in ("TOTALOUTSTANDING", "TotalUnits"):
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    return df


def load_rent_benchmark(bedrooms):
    """
    Load the Vancouver CMA rent benchmark.

    Bedrooms:
        1 = one bedroom
        2 = two bedrooms
        3 = three or more bedrooms
    """
    filenames = {
        1: "rent_benchmark_1br_q2_2026.csv",
        2: "rent_benchmark_2br_q2_2026.csv",
        3: "rent_benchmark_3br_q2_2026.csv",
    }

    if bedrooms not in filenames:
        raise ValueError(
            "Bedrooms must be 1, 2, or 3 (3 means 3+)."
        )

    path = DATA_DIR / filenames[bedrooms]

    if not path.is_file():
        raise FileNotFoundError(f"Dataset not found: {path}")

    # Skip the first 12 lines; the data table begins on line 13.
    df = pd.read_csv(
        path,
        skiprows=12,
        header=None,
        names=[
            "Geography",
            "Average asking rent",
            "Average paid rent",
        ],
        dtype=str,
        encoding="utf-8-sig",
    )

    df["Geography"] = df["Geography"].str.strip()

    # Remove thousands separators before converting to numbers.
    # Unavailable values such as ".." become NaN, not zero.
    for column in (
        "Average asking rent",
        "Average paid rent",
    ):
        cleaned = df[column].str.replace(
            ",",
            "",
            regex=False,
        )
        df[column] = pd.to_numeric(
            cleaned,
            errors="coerce",
        )

    vancouver = df[
        df["Geography"].eq(
            "Vancouver, Census metropolitan area (CMA)"
        )
    ]

    if len(vancouver) != 1:
        raise ValueError(
            f"Expected exactly one Vancouver benchmark row "
            f"for {bedrooms} bedroom(s); found {len(vancouver)}."
        )

    return vancouver.iloc[0].to_dict()


# --------------------------------------------------
# Police crime data loader
# --------------------------------------------------

import re

CRIME_DIR = DATA_DIR / "police_crime" / "extracted_csvs"


def _normalize_crime_filename(filename):
    """Normalize filenames so spaces and capitalization do not matter."""
    stem = Path(filename).stem
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return re.sub(r"[^a-z0-9]", "", stem.lower())


def _find_crime_file(area, year):
    """Find the crime CSV for a specific area and year."""
    if not CRIME_DIR.is_dir():
        raise FileNotFoundError(
            f"Crime data folder not found: {CRIME_DIR}"
        )

    if area == "downtown":
        def matches_area(name):
            return (
                ("cbd" in name or "downtown" in name)
                and "westend" not in name
            )
    else:
        def matches_area(name):
            return "westend" in name

    matches = []

    for path in CRIME_DIR.glob("*.csv"):
        name = _normalize_crime_filename(path.name)

        if str(year) in name and matches_area(name):
            matches.append(path)

    # Prefer files beginning with "crime" if available.
    preferred = [
        path for path in matches
        if _normalize_crime_filename(path.name).startswith("crime")
    ]

    if preferred:
        matches = preferred

    if not matches:
        raise FileNotFoundError(
            f"No crime CSV found for {area}, {year}. "
            f"Expected it inside: {CRIME_DIR}"
        )

    if len(matches) > 1:
        filenames = ", ".join(path.name for path in matches)
        raise ValueError(
            f"More than one crime CSV matches {area}, {year}: "
            f"{filenames}"
        )

    return matches[0]


def load_police_crime(area, years=None):
    """
    Load police-reported incidents for Downtown or West End.

    Pass years=2025 for one year, years=[2024, 2025] for
    selected years, or leave years unspecified for 2023–2026.
    """
    area = area.strip().lower().replace("_", " ")

    if area in {"downtown", "cbd", "central business district"}:
        area = "downtown"
    elif area == "west end":
        area = "west_end"
    else:
        raise ValueError(
            "Area must be 'downtown' or 'west end'."
        )

    if years is None:
        years_to_load = [2023, 2024, 2025, 2026]
    elif isinstance(years, int):
        years_to_load = [years]
    else:
        years_to_load = [int(year) for year in years]

    if not years_to_load:
        raise ValueError("Provide at least one year.")

    if any(year not in {2023, 2024, 2025, 2026}
           for year in years_to_load):
        raise ValueError(
            "Supported years are 2023, 2024, 2025 and 2026."
        )

    required_columns = {
        "TYPE", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE",
        "HUNDRED_BLOCK", "NEIGHBOURHOOD", "X", "Y",
    }

    frames = []

    for year in sorted(set(years_to_load)):
        path = _find_crime_file(area, year)

        df = pd.read_csv(
            path,
            dtype=str,
            encoding="utf-8-sig",
        )

        df.columns = df.columns.str.strip()

        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(
                f"{path.name} is missing columns: "
                f"{sorted(missing)}"
            )

        for column in [
            "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "X", "Y"
        ]:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

        df["SOURCE_FILE"] = path.name
        frames.append(df)

    return pd.concat(frames, ignore_index=True)
