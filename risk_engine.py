"""RentalShield four-part, explainable rental-concern scoring engine.

Each of the four checks has a maximum contribution of 25 points:
address verification, nearby incident context, rent benchmark deviation, and
listing/chat warning phrases. This is a heuristic concern indicator, not a
validated fraud probability. Crime is area context, not evidence of landlord fraud.
"""
from __future__ import annotations

import re
from typing import Any

from address_checker import check_property_address
from listing_analyzer import analyze_listing
from rent_checker import check_rent

MAX_POINTS_PER_CHECK = 25
SCORE_BANDS = {
    "low": (0, 24),
    "medium": (25, 49),
    "high": (50, 100),
}
TEXT_WARNING_POINTS = {"high": 12, "medium": 7, "low": 3}
CRIME_POINTS = {"Low": 0, "Medium": 13, "High": 25}


def _parse_coordinates(value: Any) -> tuple[float, float] | None:
    """Parse 'latitude, longitude' from geo_point_2d."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 2:
        parts = value[:2]
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "null"}:
            return None
        parts = re.split(r"\s*,\s*", text, maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _score_rent(rent_check: dict[str, Any] | None) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """Score the size of the rent's percentage deviation from the CMA benchmark."""
    component = {
        "key": "rent_difference",
        "label": "Rent benchmark difference",
        "points": 0,
        "max_points": MAX_POINTS_PER_CHECK,
        "assessed": False,
        "status": "Not assessed",
        "explanation": "Enter monthly rent and bedroom count to compare the rent benchmark.",
    }
    if not rent_check or rent_check.get("status") != "success":
        return 0, component, []

    try:
        difference_pct = float(rent_check.get("percentage_difference"))
    except (TypeError, ValueError):
        return 0, component, []

    absolute_gap = abs(difference_pct)
    if absolute_gap < 5:
        points, band = 0, "Less than 5%"
    elif absolute_gap < 10:
        points, band = 5, "5% to less than 10%"
    elif absolute_gap < 20:
        points, band = 10, "10% to less than 20%"
    elif absolute_gap < 30:
        points, band = 18, "20% to less than 30%"
    else:
        points, band = 25, "30% or more"

    direction = "below" if difference_pct < 0 else "above" if difference_pct > 0 else "equal to"
    benchmark = rent_check.get("benchmark_asking_rent")
    listed_rent = rent_check.get("monthly_rent")
    explanation = (
        f"The entered rent of CAD ${float(listed_rent):,.0f} is "
        f"{absolute_gap:.1f}% {direction} the Vancouver CMA asking-rent benchmark "
        f"of CAD ${float(benchmark):,.0f}. The size of this difference adds "
        f"{points}/25 pricing-concern points. The CMA figure is a market-level "
        "comparison, not a valuation of this individual unit."
    )
    component.update({
        "points": points,
        "assessed": True,
        "status": "Checked",
        "difference_percent": round(difference_pct, 1),
        "threshold_band": band,
        "explanation": explanation,
    })

    factors = []
    if points:
        factors.append({
            "type": "rent_benchmark_difference",
            "severity": "caution" if points < 18 else "high_caution",
            "points": points,
            "description": explanation,
        })
    return points, component, factors


def _make_recommendations(
    address_check: dict[str, Any],
    rent_check: dict[str, Any] | None,
    listing_analysis: dict[str, Any] | None,
    crime_context: dict[str, Any] | None,
) -> list[str]:
    """Return practical next steps based on findings."""
    actions: list[str] = []
    warning_types = {
        item.get("type")
        for item in (listing_analysis or {}).get("warnings", [])
    }

    if address_check.get("status") == "not_found":
        actions.append(
            "The address did not match the searched property records. Confirm the full address and independently verify the landlord or agent's authority before paying."
        )
    elif address_check.get("status") == "found":
        actions.append(
            "The address appears in the City property-address dataset; independently confirm who is authorized to rent the unit and whether it is actually available."
        )

    if "deposit_before_viewing" in warning_types:
        actions.append(
            "Do not send a deposit before viewing the unit and independently verifying the person offering it."
        )
    if "payment_within_24_hours" in warning_types or "urgent_payment_pressure" in warning_types:
        actions.append(
            "Pause if you are being rushed. Verify the listing and payment recipient using independently obtained contact details."
        )
    if "unusual_payment_method" in warning_types:
        actions.append(
            "Do not pay through gift cards or cryptocurrency; be cautious with irreversible transfers to an unverified recipient."
        )
    if "landlord_cannot_show_property" in warning_types:
        actions.append(
            "Request an in-person viewing or a live video walkthrough, and verify the landlord or agent's authority independently."
        )
    if "deposit_to_secure_listing" in warning_types:
        actions.append(
            "Before paying to reserve the unit, confirm the viewing, written tenancy terms, recipient identity and applicable deposit rules."
        )

    if rent_check and rent_check.get("status") == "success":
        try:
            difference_pct = float(rent_check.get("percentage_difference", 0))
        except (TypeError, ValueError):
            difference_pct = 0
        if abs(difference_pct) >= 10:
            actions.append(
                "Compare the asking rent with several current listings of similar size and condition, and confirm what utilities or services are included."
            )

    crime = (crime_context or {}).get("aggregate_summary") or {}
    if crime.get("relative_activity_level") == "High":
        actions.append(
            "Review the nearby incident context and consider the alternative neighbourhoods below. Area-level incident counts do not describe the individual building or establish listing fraud."
        )

    actions.append(
        "Review the written tenancy agreement carefully, keep copies of messages and receipts, and never rely on this score alone to make a rental decision."
    )
    return list(dict.fromkeys(actions))


def generate_rental_report(
    civic_number,
    street,
    area=None,
    monthly_rent=None,
    bedrooms=None,
    description=None,
    crime_years=None,
):
    """Combine address, rent, nearby crime, and listing-text checks."""
    if isinstance(area, str) and not area.strip():
        area = None

    report: dict[str, Any] = {
        "address_check": None,
        "rent_check": None,
        "crime_context": {
            "status": "not_attempted",
            "message": "Nearby crime context has not been checked.",
        },
        "listing_analysis": None,
        "risk_score": 0,
        "indicator_score": 0,
        "risk_level": "low",
        "risk_level_label": "Low overall rental concern",
        "assessment_completeness": "partial",
        "checks_completed": 0,
        "score_components": {
            "address_verification_points": 0,
            "crime_context_points": 0,
            "rent_difference_points": 0,
            "below_benchmark_points": 0,  # legacy key retained for compatibility
            "text_warning_points": 0,
        },
        "score_component_details": [],
        "risk_factors": [],
        "explanation": "",
        "recommended_actions": [],
        "alternative_areas": {
            "status": "not_evaluated",
            "message": "Alternative areas are suggested for Medium and High overall scores.",
            "areas": [],
        },
        "limitations": [],
        "score_methodology": {
            "maximum_points_per_check": 25,
            "maximum_total_points": 100,
            "components": [
                "Address verification",
                "Nearby crime activity",
                "Rent benchmark difference",
                "Listing/chat warning phrases",
            ],
            "risk_bands": {
                "0-24": "Low overall rental concern",
                "25-49": "Medium overall rental concern",
                "50-100": "High overall rental concern",
            },
            "note": (
                "Provisional rule-based concern indicator, not a validated probability "
                "of fraud or an official crime/safety score."
            ),
        },
        "disclaimer": (
            "RentalShield provides preliminary screening information, not a guarantee "
            "of legitimacy or safety. The 100-point concern score gives each of four "
            "checks up to 25 points. It is a provisional heuristic, not a validated "
            "probability of fraud. High nearby incident activity describes the area, "
            "not the landlord or individual building. Property-address matches do not "
            "verify ownership, authority, availability or listing legitimacy."
        ),
    }

    # 1. Address verification
    address_check = check_property_address(civic_number, street, area)
    report["address_check"] = address_check
    address_status = address_check.get("status")
    address_assessed = address_status in {"found", "not_found"}

    if address_status == "found":
        address_points = 0
        address_text = (
            "A matching address appears in the supplied City property-address dataset. "
            "This does not verify ownership, authority to rent or unit availability."
        )
    elif address_status == "not_found":
        address_points = 25
        address_text = (
            "No exact match was found in the searched property-address dataset. "
            "This adds 25/25 address-verification concern points, but a missing match "
            "does not prove fraud."
        )
        report["limitations"].append(
            "The address was not found in the searched Downtown/West End address data. "
            "A missing match is not proof that a property is invalid or fraudulent."
        )
    else:
        address_points = 0
        address_text = (
            "Address verification could not be completed with the supplied address/area."
        )
        report["limitations"].append(
            "Address verification was not completed; this component was not assessed."
        )

    address_component = {
        "key": "address_verification",
        "label": "Address verification",
        "points": address_points,
        "max_points": 25,
        "assessed": address_assessed,
        "status": "Matched" if address_status == "found" else "Not matched" if address_status == "not_found" else "Not assessed",
        "explanation": address_text,
    }
    report["score_components"]["address_verification_points"] = address_points

    if address_points:
        report["risk_factors"].append({
            "type": "address_not_found",
            "severity": "caution",
            "points": address_points,
            "description": address_text,
        })

    # 2. Rent benchmark comparison
    rent_check = None
    if monthly_rent is not None and bedrooms is not None:
        try:
            bedrooms_value = int(bedrooms)
        except (TypeError, ValueError):
            bedrooms_value = bedrooms
        rent_check = check_rent(monthly_rent, bedrooms_value)
        report["rent_check"] = rent_check
    else:
        report["limitations"].append(
            "Rent benchmark comparison was not performed because monthly rent or bedroom count was not supplied."
        )

    rent_points, rent_component, rent_factors = _score_rent(rent_check)
    report["score_components"]["rent_difference_points"] = rent_points
    report["score_components"]["below_benchmark_points"] = rent_points
    report["risk_factors"].extend(rent_factors)

    # 3. Listing or copied landlord/chat text
    if isinstance(description, str) and description.strip():
        listing_result = analyze_listing(description)
        report["listing_analysis"] = listing_result
    else:
        listing_result = None
        report["limitations"].append(
            "Listing text was not supplied or extracted; text screening was not performed."
        )

    warning_points_raw = 0
    text_factors = []
    if listing_result and listing_result.get("status") == "success":
        for warning in listing_result.get("warnings", []):
            severity = str(warning.get("severity", "low")).lower()
            points = TEXT_WARNING_POINTS.get(severity, TEXT_WARNING_POINTS["low"])
            warning_points_raw += points
            if points:
                text_factors.append({
                    "type": warning.get("type", "listing_text_warning"),
                    "severity": severity,
                    "points": points,
                    "description": warning.get(
                        "description",
                        "A configured warning phrase was detected in the supplied text.",
                    ),
                    "matched_phrases": warning.get("matched_phrases", []),
                })
    text_points = min(25, warning_points_raw)
    report["score_components"]["text_warning_points"] = text_points
    report["risk_factors"].extend(text_factors)
    text_assessed = bool(listing_result and listing_result.get("status") == "success")
    if text_assessed:
        if text_points:
            text_text = (
                f"Configured warning phrases were detected. This adds {text_points}/25 "
                "listing-text concern points; review the matched wording in context."
            )
        else:
            text_text = (
                "No configured warning phrases were detected in the supplied text. "
                "This does not establish that the listing is genuine."
            )
    else:
        text_text = (
            "Not assessed because no non-empty listing description or landlord/chat text was supplied."
        )
    text_component = {
        "key": "text_warning",
        "label": "Listing/chat warning wording",
        "points": text_points,
        "max_points": 25,
        "assessed": text_assessed,
        "status": "Warnings found" if text_points else "Checked — no configured warnings" if text_assessed else "Not assessed",
        "explanation": text_text,
    }

    # 4. Nearby police-reported incident context, using coordinates from a matched address.
    matches = (address_check or {}).get("matches") or []
    matched_property = matches[0] if matches else None
    coordinates = _parse_coordinates(matched_property.get("coordinates")) if matched_property else None
    crime_area = (matched_property or {}).get("area") or area
    if coordinates is not None and crime_area:
        try:
            from crime_checker import check_nearby_crime

            latitude, longitude = coordinates
            report["crime_context"] = check_nearby_crime(
                latitude,
                longitude,
                crime_area,
                years=crime_years,
                radii_meters=(250, 500),
                top_n_types=5,
            )
        except Exception as exc:
            report["crime_context"] = {
                "status": "unavailable",
                "message": "Nearby crime context could not be calculated.",
                "technical_detail": str(exc),
            }
            report["limitations"].append(
                "Nearby crime context could not be calculated. Confirm the citywide CSV and rebuilt comparison-baseline files are present."
            )
    else:
        report["crime_context"] = {
            "status": "unavailable",
            "message": (
                "Nearby crime context could not be calculated because no matched "
                "address with usable coordinates was available."
            ),
        }
        report["limitations"].append(
            "Nearby crime context was not assessed because the address did not match a record with usable coordinates."
        )

    crime_context = report.get("crime_context") or {}
    aggregate = crime_context.get("aggregate_summary") or {}
    crime_assessed = (
        crime_context.get("status") == "available"
        and aggregate.get("relative_activity_level") in {"Low", "Medium", "High"}
    )
    crime_level = aggregate.get("relative_activity_level")
    crime_points = CRIME_POINTS.get(crime_level, 0) if crime_assessed else 0
    if crime_assessed:
        count = aggregate.get("reported_incident_records", 0)
        types = aggregate.get("top_incident_type_names", [])
        type_phrase = ", ".join(types) if types else "the incident types in the source data"
        percentile = aggregate.get("comparison_percentile")
        if percentile is not None:
            comparison_phrase = f" The combined-period count ranks around the {float(percentile):.1f}th percentile among sampled Vancouver locations."
        else:
            comparison_phrase = ""
        crime_text = (
            f"Relative nearby incident activity is {crime_level.lower()} based on "
            f"{count:,} reported incident records within approximately 500 m across "
            f"{aggregate.get('timeframe_label', 'the selected period')}. Common types include "
            f"{type_phrase}.{comparison_phrase} This describes the surrounding area, not the listing's legitimacy."
        )
    else:
        crime_text = (
            "Not assessed because a matched address coordinate and citywide comparison were unavailable."
        )
    crime_component = {
        "key": "crime_context",
        "label": "Nearby crime activity",
        "points": crime_points,
        "max_points": 25,
        "assessed": crime_assessed,
        "status": f"{crime_level} relative activity" if crime_assessed else "Not assessed",
        "explanation": crime_text,
    }
    report["score_components"]["crime_context_points"] = crime_points
    if crime_assessed and crime_points:
        report["risk_factors"].append({
            "type": "nearby_incident_activity",
            "severity": crime_level.lower(),
            "points": crime_points,
            "description": crime_text,
        })

    # Four equal-weight components (25 points maximum each).
    components = [address_component, crime_component, rent_component, text_component]
    report["score_component_details"] = components
    report["checks_completed"] = sum(1 for component in components if component["assessed"])
    report["assessment_completeness"] = (
        "complete" if report["checks_completed"] == 4 else "partial"
    )
    total_score = min(100, sum(int(component["points"]) for component in components))
    report["risk_score"] = total_score
    report["indicator_score"] = total_score

    if total_score <= 24:
        level, label = "low", "Low overall rental concern"
    elif total_score <= 49:
        level, label = "medium", "Medium overall rental concern"
    else:
        level, label = "high", "High overall rental concern"
    report["risk_level"] = level
    report["risk_level_label"] = label

    component_summary = "; ".join(
        f"{item['label']}: {item['points']}/25"
        + (" (not assessed)" if not item["assessed"] else "")
        for item in components
    )
    completion_note = ""
    if report["assessment_completeness"] == "partial":
        completion_note = (
            f" Only {report['checks_completed']} of 4 checks were completed; "
            "unassessed checks add no points, so this score may understate concern."
        )
    report["explanation"] = (
        f"Overall Rental Concern Score: {total_score}/100 — {label}. "
        f"Breakdown: {component_summary}.{completion_note} "
        "This is a rule-based screening aid, not a probability of fraud."
    )

    # Suggest up to three nearby area names for Medium/High scores, using the
    # entered rent as a search target (not as an asserted area-specific average).
    if total_score >= 25 and crime_assessed and coordinates is not None:
        try:
            from crime_checker import suggest_alternative_areas

            latitude, longitude = coordinates
            report["alternative_areas"] = suggest_alternative_areas(
                latitude=latitude,
                longitude=longitude,
                current_neighbourhood=(matched_property or {}).get("area") or area,
                monthly_rent=monthly_rent,
                bedrooms=bedrooms,
                limit=3,
                years=crime_years,
            )
            if report["alternative_areas"].get("areas"):
                report["recommended_actions"].append(
                    "Explore the nearby neighbourhood suggestions below if you want to compare other areas with lower relative incident activity."
                )
        except Exception as exc:
            report["alternative_areas"] = {
                "status": "unavailable",
                "message": "Alternative area suggestions could not be calculated.",
                "areas": [],
                "technical_detail": str(exc),
            }
            report["limitations"].append(
                "Alternative neighbourhood suggestions were unavailable. Rebuild the citywide baseline to include neighbourhood labels."
            )
    elif total_score < 25:
        report["alternative_areas"] = {
            "status": "not_triggered",
            "message": "Alternative areas are shown when the overall score is Medium or High.",
            "areas": [],
        }
    else:
        report["alternative_areas"] = {
            "status": "unavailable",
            "message": (
                "Alternative areas could not be calculated because nearby crime context "
                "or matched address coordinates were unavailable."
            ),
            "areas": [],
        }

    report["recommended_actions"].extend(
        _make_recommendations(
            address_check,
            rent_check,
            listing_result,
            crime_context,
        )
    )
    report["recommended_actions"] = list(dict.fromkeys(report["recommended_actions"]))

    if not report["alternative_areas"].get("areas") and total_score >= 25:
        message = report["alternative_areas"].get("message")
        if message:
            report["limitations"].append(str(message))

    return report
