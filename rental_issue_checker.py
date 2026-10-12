
import re

import pandas as pd

from data_loader import load_rental_standards
from address_checker import normalize_text


def check_rental_issues(civic_number, street, area=None):
    """
    Check available rental-standard records for a property.

    Searches Downtown and West End unless an area is supplied.
    A missing record does not prove that a property has no issues.
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
                "message": (
                    "Search is currently limited to Downtown and West End."
                ),
                "matches": [],
            }

        areas = [
            "downtown" if normalized_area == "DOWNTOWN" else "west end"
        ]
    else:
        areas = ["downtown", "west end"]

    matches = []

    for current_area in areas:
        df = load_rental_standards(current_area)

        street_numbers = df["StreetNumber"].map(normalize_text)
        street_names = df["Street"].map(normalize_text)

        found = df[
            street_numbers.eq(civic_number)
            & street_names.eq(street)
        ]

        for _, row in found.iterrows():
            outstanding = row["TOTALOUTSTANDING"]

            matches.append({
                "street_number": row["StreetNumber"],
                "street": row["Street"],
                "area": row["Geo Local Area"],
                "outstanding_issues": (
                    int(outstanding)
                    if pd.notna(outstanding)
                    else None
                ),
                "total_units": (
                    int(row["TotalUnits"])
                    if pd.notna(row["TotalUnits"])
                    else None
                ),
                "business_operator": row.get("BUSINESSOPERATOR"),
                "detail_url": row.get("DetailURL"),
            })

    if matches:
        return {
            "status": "found",
            "message": (
                "A matching rental-standard record was found. "
                "Review the reported details and source record."
            ),
            "matches": matches,
        }

    return {
        "status": "not_found",
        "message": (
            "No matching record was found in the available rental-standard "
            "datasets. This does not prove that the property has no issues."
        ),
        "matches": [],
    }
