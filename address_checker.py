
import re

import pandas as pd

from data_loader import load_property_addresses


def normalize_text(value):
    """Normalize civic numbers and street names for reliable matching."""
    if value is None or pd.isna(value):
        return ""

    value = str(value).strip().upper()

    # Fix numeric values read from CSV as floats, such as 1477.0.
    if re.fullmatch(r"\d+\.0+", value):
        value = value.split(".", 1)[0]

    value = re.sub(r"[.,#]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    aliases = {
        "STREET": "ST", "ST": "ST",
        "AVENUE": "AVE", "AV": "AVE", "AVE": "AVE",
        "BOULEVARD": "BLVD", "BLVD": "BLVD",
        "DRIVE": "DR", "DR": "DR",
        "ROAD": "RD", "RD": "RD",
        "CRESCENT": "CRES", "CRES": "CRES",
        "COURT": "CT", "CT": "CT",
        "PLACE": "PL", "PL": "PL",
        "TERRACE": "TER", "TER": "TER",
        "LANE": "LN", "LN": "LN",
        "HIGHWAY": "HWY", "HWY": "HWY",
        "PARKWAY": "PKWY", "PKWY": "PKWY",
        "TRAIL": "TRL", "TRL": "TRL",
    }

    tokens = [aliases.get(token, token) for token in value.split()]

    if tokens:
        directions = {
            "WEST": "W", "EAST": "E",
            "NORTH": "N", "SOUTH": "S",
            "NORTHWEST": "NW", "NORTHEAST": "NE",
            "SOUTHWEST": "SW", "SOUTHEAST": "SE",
        }
        tokens[0] = directions.get(tokens[0], tokens[0])

    return " ".join(tokens)


def check_property_address(civic_number, street, area=None):
    """
    Search the available Downtown and West End address datasets.

    Returns a dictionary describing the lookup result.
    A match confirms dataset presence, not ownership or legitimacy.
    """
    civic_number = normalize_text(civic_number)
    street = normalize_text(street)

    if not civic_number or not street:
        return {
            "status": "invalid_input",
            "message": "Enter both a street number and street name.",
            "matches": [],
        }

    if area is not None:
        normalized_area = normalize_text(area)

        if normalized_area not in {"DOWNTOWN", "WEST END"}:
            return {
                "status": "unsupported_area",
                "message": "Search is currently limited to Downtown and West End.",
                "matches": [],
            }

        areas = [
            "downtown" if normalized_area == "DOWNTOWN" else "west end"
        ]
    else:
        areas = ["downtown", "west end"]

    matches = []

    for current_area in areas:
        df = load_property_addresses(current_area)

        civic_values = df["CIVIC_NUMBER"].map(normalize_text)
        street_values = df["STD_STREET"].map(normalize_text)

        found = df[
            civic_values.eq(civic_number)
            & street_values.eq(street)
        ]

        for _, row in found.iterrows():
            matches.append({
                "civic_number": row["CIVIC_NUMBER"],
                "street": row["STD_STREET"],
                "area": row["Geo Local Area"],
                "site_id": row.get("SITE_ID"),
                "coordinates": row.get("geo_point_2d"),
            })

    if matches:
        return {
            "status": "found",
            "message": "The address was found in the available property records.",
            "matches": matches,
        }

    return {
        "status": "not_found",
        "message": (
            "No exact match was found in the available datasets. "
            "This does not mean the property or listing is fraudulent."
        ),
        "matches": [],
    }
