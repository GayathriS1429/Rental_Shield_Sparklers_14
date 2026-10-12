
from data_loader import load_rent_benchmark


def check_rent(monthly_rent, bedrooms):
    """
    Compare a listing's monthly rent with the Vancouver CMA benchmark.

    bedrooms:
        1 = one bedroom
        2 = two bedrooms
        3 = three or more bedrooms
    """
    try:
        rent = float(monthly_rent)
    except (TypeError, ValueError):
        return {
            "status": "invalid_input",
            "message": "Enter a valid monthly rent.",
        }

    if rent <= 0:
        return {
            "status": "invalid_input",
            "message": "Monthly rent must be greater than zero.",
        }

    if isinstance(bedrooms, bool) or bedrooms not in (1, 2, 3):
        return {
            "status": "invalid_input",
            "message": "Bedrooms must be 1, 2, or 3 (3 means 3+).",
        }

    benchmark = load_rent_benchmark(bedrooms)
    asking_benchmark = benchmark["Average asking rent"]
    paid_benchmark = benchmark["Average paid rent"]

    if asking_benchmark is None or asking_benchmark <= 0:
        return {
            "status": "benchmark_unavailable",
            "message": "The asking-rent benchmark is unavailable.",
        }

    difference = rent - asking_benchmark
    percentage_difference = (difference / asking_benchmark) * 100

    if difference > 0:
        comparison = "above"
    elif difference < 0:
        comparison = "below"
    else:
        comparison = "equal to"

    return {
        "status": "success",
        "monthly_rent": round(rent, 2),
        "bedrooms": bedrooms,
        "benchmark_asking_rent": float(asking_benchmark),
        "benchmark_paid_rent": (
            float(paid_benchmark)
            if paid_benchmark is not None
            else None
        ),
        "difference": round(difference, 2),
        "percentage_difference": round(percentage_difference, 2),
        "comparison": comparison,
        "message": (
            f"The listing rent is {abs(percentage_difference):.1f}% "
            f"{comparison} the Vancouver CMA asking-rent benchmark."
        ),
        "disclaimer": (
            "This is a market-level comparison, not an individual "
            "property valuation or proof of fraud."
        ),
    }
