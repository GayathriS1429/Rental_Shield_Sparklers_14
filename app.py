import ipaddress
import json
import math
import os
import re
import socket
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import phonenumbers
from dotenv import load_dotenv
from flask import (
    Flask, abort, flash, jsonify, redirect, render_template,
    request, send_file, url_for
)
from flask_login import (
    LoginManager, current_user, login_required, login_user, logout_user
)
from flask_wtf.csrf import CSRFProtect
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
)
from sqlalchemy import func
from werkzeug.exceptions import RequestEntityTooLarge
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

from models import db, User, VerificationReport
from risk_engine import generate_rental_report
from analytics_dashboard import build_analytics_context


# --------------------------------------------------
# APPLICATION CONFIGURATION
# --------------------------------------------------

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY"),
    SQLALCHEMY_DATABASE_URI=(
        "sqlite:///" + (INSTANCE_DIR / "rentalshield.db").as_posix()
    ),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

@app.context_processor
def inject_carto_map_config():
    return {
        "carto_basemap_api_key": os.getenv(
            "CARTO_BASEMAP_API_KEY", ""
        )
    }

if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "SECRET_KEY is missing. Add it to your project-root .env file."
    )

db.init_app(app)
CSRFProtect(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."
login_manager.init_app(app)


# --------------------------------------------------
# USER SESSION MANAGEMENT
# --------------------------------------------------

@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------
# GENERAL HELPERS
# --------------------------------------------------

def json_safe(value):
    """Convert values into strict JSON-compatible values."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if hasattr(value, "item"):
        return json_safe(value.item())

    if isinstance(value, float) and not math.isfinite(value):
        return None

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def valid_email(email):
    """Validate a conventional email format."""
    if not email or len(email) > 254 or email != email.strip():
        return False

    if any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False

    local, domain = email.rsplit("@", 1)

    if not local or len(local) > 64 or not domain:
        return False

    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False

    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        return False

    if len(domain) > 253 or "." not in domain:
        return False

    labels = domain.split(".")

    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not re.fullmatch(r"[A-Za-z0-9-]+", label)
        ):
            return False

    return len(labels[-1]) >= 2 and labels[-1].isalpha()


def validate_phone(country_code, national_number):
    """Validate an optional phone number and return its E.164 format."""
    if not national_number:
        return None, None

    allowed_codes = {
        "+1": "US",
        "+91": "IN",
        "+44": "GB",
        "+61": "AU",
        "+971": "AE",
        "+65": "SG",
        "+64": "NZ",
        "+49": "DE",
        "+33": "FR",
        "+81": "JP",
    }

    region = allowed_codes.get(country_code)

    if not region:
        return None, "Choose a supported country calling code."

    if not re.fullmatch(r"[0-9 ()-]{7,20}", national_number):
        return None, (
            "Enter a phone number using digits, spaces, parentheses or hyphens."
        )

    try:
        parsed = phonenumbers.parse(national_number, region)
    except phonenumbers.NumberParseException:
        return None, "Enter a valid phone number for the selected country."

    if not phonenumbers.is_possible_number(parsed):
        return None, (
            "The phone number length is not valid for the selected country."
        )

    if not phonenumbers.is_valid_number(parsed):
        return None, "Enter a valid phone number for the selected country."

    if parsed.country_code != int(country_code.lstrip("+")):
        return None, (
            "The phone number does not match the selected country code."
        )

    return (
        phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        ),
        None,
    )


def validate_listing_url(value):
    """
    Validate a public HTTP/HTTPS URL and reject non-public IP addresses.
    """
    if not value:
        return None, None

    value = value.strip()

    if len(value) > 2048:
        return None, "The listing URL is too long."

    try:
        parsed = urlparse(value)

        if parsed.scheme not in ("http", "https"):
            return None, (
                "Listing URLs must start with http:// or https://."
            )

        if not parsed.hostname or parsed.username or parsed.password:
            return None, "Enter a valid public rental listing URL."

        host = parsed.hostname.lower().rstrip(".")

        if (
            host == "localhost"
            or host.endswith(".localhost")
            or host.endswith(".local")
        ):
            return None, "Local and private URLs are not allowed."

        try:
            ip = ipaddress.ip_address(host)

            if not ip.is_global:
                return None, (
                    "Private or non-public listing URLs are not allowed."
                )

        except ValueError:
            try:
                addresses = socket.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror:
                return None, "The listing domain could not be resolved."

            if not addresses:
                return None, "The listing domain could not be resolved."

            for result in addresses:
                address = result[4][0].split("%")[0]
                ip = ipaddress.ip_address(address)

                if not ip.is_global:
                    return None, (
                        "The listing URL resolves to a private or non-public "
                        "address."
                    )

        return value, None

    except (ValueError, TypeError):
        return None, "Enter a valid listing URL."


# --------------------------------------------------
# LISTING TEXT EXTRACTION
# --------------------------------------------------

def extract_listing_text(listing_url):
    """
    Retrieve visible text from a public listing page using Playwright.
    Extraction is best-effort. Some websites block automated requests.
    """
    if not listing_url:
        return None, "No listing URL supplied."

    checked_url, error = validate_listing_url(listing_url)

    if error:
        return None, error

    extracted_text = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
            )

            page = context.new_page()
            page.set_default_navigation_timeout(15000)
            page.set_default_timeout(7000)

            def protect_request(route):
                _, route_error = validate_listing_url(route.request.url)

                if route_error:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", protect_request)

            try:
                response = page.goto(
                    checked_url,
                    wait_until="domcontentloaded",
                    timeout=15000,
                )

                if response and response.status >= 400:
                    return (
                        None,
                        f"The listing website returned HTTP {response.status}.",
                    )

                try:
                    extracted_text = page.locator("body").inner_text(
                        timeout=7000
                    )
                except PlaywrightTimeoutError:
                    extracted_text = None

            finally:
                context.close()
                browser.close()

    except Exception:
        app.logger.exception("Playwright listing extraction failed")

        return None, (
            "Automatic extraction was unavailable. Please paste the listing "
            "description manually."
        )

    if not extracted_text:
        return None, "No readable listing text was extracted."

    return extracted_text[:15000], None


def extract_listing_details(text):
    """
    Extract likely rental details from listing text.

    Building-level rent and bedroom ranges are provisional and must be
    checked against the exact unit selected by the renter.
    """
    details = {
        "street_number": "",
        "street": "",
        "area": "",
        "monthly_rent": "",
        "bedrooms": "",
        "description": text[:15000] if text else "",
        "extraction_notes": [],
    }

    if not text:
        details["extraction_notes"].append(
            "No listing text was available to analyze."
        )
        return details

    normalized_text = re.sub(r"[\t ]+", " ", text)

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in normalized_text.splitlines()
        if line.strip()
    ]

    # Example: 3435 Sawmill Cres, Vancouver, BC V5S 0K6, CAN
    street_suffixes = (
        r"Street|St|Crescent|Cres|Avenue|Ave|Road|Rd|Drive|Dr|"
        r"Boulevard|Blvd|Lane|Ln|Way|Court|Ct|Place|Pl|Terrace|Ter|"
        r"Trail|Trl|Highway|Hwy|Parkway|Pkwy"
    )

    address_pattern = re.compile(
        rf"^\s*(\d{{1,6}})\s+"
        rf"([A-Za-z0-9.'’‑-]+(?:\s+[A-Za-z0-9.'’‑-]+){{0,5}}?\s+"
        rf"(?:{street_suffixes}))"
        rf"\s*,\s*([^,\n]+)",
        re.IGNORECASE,
    )

    for line in lines:
        match = address_pattern.search(line)

        if match:
            details["street_number"] = match.group(1).strip()
            details["street"] = match.group(2).strip()
            details["area"] = match.group(3).strip()
            break

    if not details["street_number"] or not details["street"]:
        details["extraction_notes"].append(
            "The street address could not be identified confidently."
        )

    if not details["area"]:
        details["extraction_notes"].append(
            "The city or neighbourhood could not be identified confidently."
        )

    # Rent ranges, for example $2,475–$3,116.
    money = r"\$\s*([\d,]+(?:\.\d{1,2})?)"

    range_pattern = re.compile(
        rf"{money}\s*(?:-|–|—|to)\s*{money}",
        re.IGNORECASE,
    )

    range_match = range_pattern.search(normalized_text)

    if range_match:
        try:
            lower_rent = float(range_match.group(1).replace(",", ""))
            upper_rent = float(range_match.group(2).replace(",", ""))

            if 1 <= lower_rent <= upper_rent <= 100000:
                details["monthly_rent"] = str(int(lower_rent))

                details["extraction_notes"].append(
                    f"The page advertises a rent range of "
                    f"${lower_rent:,.0f}–${upper_rent:,.0f}. "
                    "The rent field contains the starting rent only; "
                    "confirm the exact rent for the selected unit."
                )

        except (ValueError, TypeError):
            pass

    else:
        single_rent_patterns = [
            rf"(?:starting at|from|monthly rent|per month|rent|price)"
            rf"[^\d$]{{0,30}}{money}",
            rf"{money}\s*(?:/\s*month|per month|monthly)\b",
        ]

        for pattern in single_rent_patterns:
            match = re.search(pattern, normalized_text, re.IGNORECASE)

            if not match:
                continue

            try:
                rent = float(match.groups()[-1].replace(",", ""))

                if 1 <= rent <= 100000:
                    details["monthly_rent"] = str(int(rent))
                    break

            except (ValueError, TypeError):
                continue

    if not details["monthly_rent"]:
        details["extraction_notes"].append(
            "Monthly rent could not be identified confidently."
        )

    # Examples: Studio-3 Beds, 1-3 Bedrooms.
    bedroom_range_pattern = re.compile(
        r"\b(?:studio|studios|\d+)\s*(?:-|–|—|to)\s*"
        r"(\d+)\s*(?:bedrooms?|beds?)\b",
        re.IGNORECASE,
    )

    bedroom_range_match = bedroom_range_pattern.search(normalized_text)

    if bedroom_range_match:
        max_bedrooms = int(bedroom_range_match.group(1))

        if max_bedrooms >= 3:
            details["bedrooms"] = "3"
        elif max_bedrooms in (1, 2):
            details["bedrooms"] = str(max_bedrooms)

        details["extraction_notes"].append(
            "The page lists multiple bedroom options. The bedroom field "
            "represents the range's upper end, not a confirmed count for "
            "one specific unit."
        )

    else:
        bedroom_patterns = [
            r"\b(\d+)\s*(?:bedrooms?|beds?)\b",
            r"\b(\d+)\s*br\b",
        ]

        for pattern in bedroom_patterns:
            match = re.search(pattern, normalized_text, re.IGNORECASE)

            if not match:
                continue

            bedroom_count = int(match.group(1))

            if bedroom_count >= 3:
                details["bedrooms"] = "3"
            elif bedroom_count in (1, 2):
                details["bedrooms"] = str(bedroom_count)

            if details["bedrooms"]:
                break

    if not details["bedrooms"]:
        details["extraction_notes"].append(
            "Bedroom count could not be identified confidently."
        )

    return details


@app.post("/fetch-listing")
@login_required
def fetch_listing():
    """Fetch a public listing and return best-effort extracted details."""
    if not request.is_json:
        return jsonify({
            "success": False,
            "error": "Send the listing URL as JSON.",
        }), 400

    payload = request.get_json(silent=True) or {}
    listing_url = str(payload.get("listing_url", "")).strip()

    if not listing_url:
        return jsonify({
            "success": False,
            "error": "Paste a rental listing URL first.",
        }), 400

    checked_url, url_error = validate_listing_url(listing_url)

    if url_error:
        return jsonify({
            "success": False,
            "error": url_error,
        }), 400

    listing_text, extraction_error = extract_listing_text(checked_url)

    if extraction_error or not listing_text:
        return jsonify({
            "success": False,
            "error": extraction_error or (
                "Listing text could not be extracted. "
                "Please enter the details manually."
            ),
        }), 422

    details = extract_listing_details(listing_text)

    return jsonify({
        "success": True,
        "listing_url": checked_url,
        "details": details,
        "message": (
            "Listing text retrieved. Review the suggested values "
            "and complete any missing details."
        ),
    })


# --------------------------------------------------
# SAVED REPORT HELPER
# --------------------------------------------------

def get_owned_report(report_id):
    saved_report = db.session.get(VerificationReport, report_id)

    if saved_report is None or saved_report.user_id != current_user.id:
        abort(404)

    try:
        report_data = json.loads(saved_report.report_json)
    except (json.JSONDecodeError, TypeError):
        app.logger.exception("Saved report JSON could not be decoded")
        abort(500)

    return saved_report, report_data


# --------------------------------------------------
# LANDING PAGE
# --------------------------------------------------

@app.get("/")
def home():
    return render_template("index.html")


# --------------------------------------------------
# ABOUT RENTALSHIELD
# --------------------------------------------------

@app.get("/about")
def about():
    return render_template("about.html")


# --------------------------------------------------
# MEET THE CREATORS
# --------------------------------------------------

@app.get("/creators")
def creators():
    return render_template("creators.html")


# --------------------------------------------------
# REGISTRATION
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        country_code = request.form.get("phone_country_code", "+1").strip()
        phone_input = request.form.get("phone_number", "").strip()
        terms_accepted = request.form.get("terms") == "accepted"

        error = None

        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", username):
            error = (
                "Username must be 3–80 characters using letters, numbers, "
                "dots, underscores or hyphens."
            )
        elif not valid_email(email):
            error = "Enter a valid email address."
        elif len(password) < 12:
            error = "Your password must contain at least 12 characters."
        elif len(password) > 128:
            error = "Your password must not exceed 128 characters."
        elif password != confirmation:
            error = "The passwords do not match."
        elif not terms_accepted:
            error = (
                "Please confirm that you understand RentalShield's limitations."
            )

        phone_e164 = None

        if error is None and phone_input:
            phone_e164, phone_error = validate_phone(
                country_code, phone_input
            )

            if phone_error:
                error = phone_error

        if error is None and User.query.filter(
            func.lower(User.username) == username.lower()
        ).first():
            error = "That username is already registered."

        if error is None and User.query.filter(
            func.lower(User.email) == email
        ).first():
            error = "That email address is already registered."

        if error:
            flash(error, "error")

        else:
            user = User(
                username=username,
                email=email,
                phone_country_code=country_code if phone_e164 else None,
                phone_number=phone_e164,
            )

            user.set_password(password)
            db.session.add(user)

            try:
                db.session.commit()

            except Exception:
                db.session.rollback()
                app.logger.exception("Account creation failed")

                flash(
                    "We couldn't create your account. Please try again.",
                    "error",
                )

            else:
                login_user(user)
                flash("Your account has been created.", "success")
                return redirect(url_for("dashboard"))

    return render_template("register.html")


# --------------------------------------------------
# LOGIN
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter(
            func.lower(User.email) == identifier.lower()
        ).first()

        if user is None:
            user = User.query.filter(
                func.lower(User.username) == identifier.lower()
            ).first()

        if user and user.check_password(password):
            login_user(user)

            next_page = request.args.get("next", "")

            if next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)

            return redirect(url_for("dashboard"))

        flash("Incorrect username/email or password.", "error")

    return render_template("login.html")


# --------------------------------------------------
# LOGOUT
# --------------------------------------------------

@app.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You have signed out.", "success")
    return redirect(url_for("home"))


# --------------------------------------------------
# USER DASHBOARD
# --------------------------------------------------

@app.get("/dashboard")
@login_required
def dashboard():
    reports = (
        VerificationReport.query
        .filter_by(user_id=current_user.id)
        .order_by(VerificationReport.created_at.desc())
        .all()
    )

    return render_template("dashboard.html", reports=reports)


# --------------------------------------------------
# RENTAL VERIFICATION
# --------------------------------------------------

@app.post("/verify")
@login_required
def verify():
    civic_number = request.form.get("civic_number", "").strip()
    street = request.form.get("street", "").strip()
    area = request.form.get("area", "").strip()
    rent_text = request.form.get("monthly_rent", "").strip()
    bedrooms_text = request.form.get("bedrooms", "").strip()
    description = request.form.get("description", "").strip()
    listing_url_input = request.form.get("listing_url", "").strip()

    if not civic_number or not street:
        flash("Enter both the street number and street name.", "error")
        return redirect(url_for("home"))

    if len(civic_number) > 20 or len(street) > 200:
        flash("The address is too long.", "error")
        return redirect(url_for("home"))

    checked_url, url_error = validate_listing_url(listing_url_input)

    if url_error:
        flash(url_error, "error")
        return redirect(url_for("home"))

    rent = None
    bedrooms = None

    if rent_text or bedrooms_text:
        try:
            if not rent_text or not bedrooms_text:
                raise ValueError

            rent = float(rent_text)
            bedrooms = int(bedrooms_text)

            if (
                not math.isfinite(rent)
                or rent <= 0
                or rent > 100000
                or bedrooms not in (1, 2, 3)
            ):
                raise ValueError

        except (ValueError, TypeError):
            flash(
                "Enter a positive monthly rent and select 1, 2 or 3+ bedrooms. "
                "Leave both fields empty to skip the rent comparison.",
                "error",
            )
            return redirect(url_for("home"))

    if checked_url:
        extracted_text, extraction_message = extract_listing_text(checked_url)

        if not description and extracted_text:
            description = extracted_text

        if extraction_message:
            flash(extraction_message, "warning")

    try:
        report = generate_rental_report(
            civic_number=civic_number,
            street=street,
            area=area or None,
            monthly_rent=rent,
            bedrooms=bedrooms,
            description=description or None,
        )

        safe_report = json_safe(report)

        saved_report = VerificationReport(
            user_id=current_user.id,
            listing_url=checked_url,
            address=f"{civic_number} {street}",
            monthly_rent=rent,
            bedrooms=bedrooms,
            report_json=json.dumps(safe_report, allow_nan=False),
        )

        db.session.add(saved_report)
        db.session.commit()

        flash("Your rental report has been saved.", "success")

        return redirect(
            url_for("report_detail", report_id=saved_report.id)
        )

    except (ValueError, FileNotFoundError):
        db.session.rollback()
        app.logger.exception("Verification input or dataset error")

        flash(
            "We couldn't complete this check. Review your inputs and make "
            "sure the required datasets are available.",
            "error",
        )

        return redirect(url_for("home"))

    except Exception:
        db.session.rollback()
        app.logger.exception("Unexpected verification error")

        flash(
            "An unexpected error occurred. No report was saved.",
            "error",
        )

        return redirect(url_for("home"))


# --------------------------------------------------
# VIEW A SAVED REPORT
# --------------------------------------------------

@app.get("/reports/<int:report_id>")
@login_required
def report_detail(report_id):
    saved_report, report_data = get_owned_report(report_id)

    return render_template(
        "report_detail.html",
        saved_report=saved_report,
        report_data=report_data,
    )


# --------------------------------------------------
# DOWNLOAD REPORT AS JSON
# --------------------------------------------------

@app.get("/reports/<int:report_id>/download/json")
@login_required
def download_report_json(report_id):
    saved_report, report_data = get_owned_report(report_id)

    download_data = {
        "report_id": saved_report.id,
        "created_at": (
            saved_report.created_at.isoformat()
            if saved_report.created_at else None
        ),
        "address": saved_report.address,
        "listing_url": saved_report.listing_url,
        "monthly_rent": saved_report.monthly_rent,
        "bedrooms": saved_report.bedrooms,
        "findings": report_data,
    }

    payload = json.dumps(
        json_safe(download_data),
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return send_file(
        BytesIO(payload),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"rentalshield_report_{saved_report.id}.json",
    )


# --------------------------------------------------
# DOWNLOAD REPORT AS PDF
# --------------------------------------------------

@app.get("/reports/<int:report_id>/download/pdf")
@login_required
def download_report_pdf(report_id):
    saved_report, report_data = get_owned_report(report_id)

    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"RentalShield Report {saved_report.id}",
        author="RentalShield",
    )

    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontSize=22,
            leading=27,
            textColor=colors.HexColor("#123149"),
            alignment=TA_CENTER,
            spaceAfter=12,
        )
    )

    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading2"],
            textColor=colors.HexColor("#087f70"),
            spaceBefore=12,
            spaceAfter=6,
        )
    )

    story = [
        Paragraph("RentalShield", styles["ReportTitle"]),
        Paragraph("Rental Screening Report", styles["Heading1"]),
        Spacer(1, 8),
    ]

    created_at = (
        saved_report.created_at.strftime("%Y-%m-%d %H:%M UTC")
        if saved_report.created_at else "Unavailable"
    )

    metadata = [
        ["Report ID", str(saved_report.id)],
        ["Created", created_at],
        ["Address entered", saved_report.address],
        [
            "Monthly rent (CAD)",
            (
                f"${saved_report.monthly_rent:,.2f}"
                if saved_report.monthly_rent is not None
                else "Not supplied"
            ),
        ],
        [
            "Bedrooms",
            (
                "3+" if saved_report.bedrooms == 3
                else str(saved_report.bedrooms)
                if saved_report.bedrooms is not None
                else "Not supplied"
            ),
        ],
        ["Listing URL", saved_report.listing_url or "Not supplied"],
    ]

    metadata_table = Table(metadata, colWidths=[43 * mm, 115 * mm])

    metadata_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eaf4f4")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#172b3a")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd7df")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.extend([metadata_table, Spacer(1, 12)])

    # Overall score and the four equal-weight components.
    score = report_data.get("risk_score")
    score_label = report_data.get("risk_level_label", "Overall rental concern")
    story.append(
        Paragraph("Overall Rental Concern Score", styles["SectionHeading"])
    )
    if score is not None:
        story.append(
            Paragraph(
                f"<b>{int(score)}/100 — {escape(str(score_label))}</b>",
                styles["Heading2"],
            )
        )
    else:
        story.append(
            Paragraph("Score unavailable", styles["BodyText"])
        )
    story.append(
        Paragraph(
            f"Assessment completeness: {escape(str(report_data.get('assessment_completeness', 'unknown')))} "
            f"({int(report_data.get('checks_completed', 0))}/4 checks completed).",
            styles["BodyText"],
        )
    )
    for component in report_data.get("score_component_details", []):
        if not isinstance(component, dict):
            continue
        label = escape(str(component.get("label", "Check")))
        points = escape(str(component.get("points", 0)))
        status_label = escape(str(component.get("status", "")))
        explanation = escape(str(component.get("explanation", "")))
        story.append(
            Paragraph(
                f"<b>{label}: {points}/25 — {status_label}</b>",
                styles["BodyText"],
            )
        )
        if explanation:
            story.append(Paragraph(explanation, styles["BodyText"]))
        story.append(Spacer(1, 4))

    # Address verification.
    story.append(Paragraph("Address verification", styles["SectionHeading"]))
    address_section = report_data.get("address_check") or {}
    if isinstance(address_section, dict):
        address_message = address_section.get("message")
        if address_message:
            story.append(Paragraph(escape(str(address_message)), styles["BodyText"]))
        for match in address_section.get("matches", []) or []:
            if isinstance(match, dict):
                matched_address = " ".join(
                    str(match.get(field, "")).strip()
                    for field in ("civic_number", "street")
                    if match.get(field)
                )
                match_area = match.get("area")
                if match_area:
                    matched_address += f" — {match_area}"
                if matched_address:
                    story.append(Paragraph(escape(matched_address), styles["BodyText"]))

    # Rent benchmark.
    story.append(Paragraph("Rent benchmark", styles["SectionHeading"]))
    rent_section = report_data.get("rent_check") or {}
    if isinstance(rent_section, dict):
        rent_message = rent_section.get("message")
        if rent_message:
            story.append(Paragraph(escape(str(rent_message)), styles["BodyText"]))
        if rent_section.get("status") == "success":
            pct = rent_section.get("percentage_difference")
            if pct is not None:
                story.append(
                    Paragraph(
                        f"<b>Difference from benchmark:</b> {float(pct):+.1f}%",
                        styles["BodyText"],
                    )
                )
            for key, label in (
                ("monthly_rent", "Entered monthly rent"),
                ("benchmark_asking_rent", "Vancouver CMA asking-rent benchmark"),
                ("difference", "CAD difference"),
            ):
                value = rent_section.get(key)
                if value is not None:
                    story.append(
                        Paragraph(
                            f"<b>{escape(label)}:</b> CAD ${float(value):,.2f}",
                            styles["BodyText"],
                        )
                    )

    # Concise three-line, combined-period crime context.
    story.append(
        Paragraph("Crime activity near the address", styles["SectionHeading"])
    )
    crime_section = report_data.get("crime_context") or {}
    summary_lines = crime_section.get("summary_lines") or (
        crime_section.get("aggregate_summary") or {}
    ).get("summary_lines") or []
    if crime_section.get("status") == "available" and summary_lines:
        for line in summary_lines[:3]:
            story.append(Paragraph(escape(str(line)), styles["BodyText"]))
    elif crime_section.get("status") == "available":
        story.append(
            Paragraph(
                "This saved report uses the older year-by-year crime format. Create a new rental check to generate the combined-period summary.",
                styles["BodyText"],
            )
        )
    else:
        story.append(
            Paragraph(
                escape(str(crime_section.get("message", "Nearby crime context was unavailable."))),
                styles["BodyText"],
            )
        )

    # Listing/chat text screening.
    story.append(
        Paragraph("Listing and chat warning wording", styles["SectionHeading"])
    )
    listing_section = report_data.get("listing_analysis")
    if isinstance(listing_section, dict):
        listing_message = listing_section.get("message")
        if listing_message:
            story.append(Paragraph(escape(str(listing_message)), styles["BodyText"]))
        warnings = listing_section.get("warnings") or []
        for warning in warnings:
            if not isinstance(warning, dict):
                continue
            warning_title = " — ".join(
                str(warning.get(key, ""))
                for key in ("type", "severity")
                if warning.get(key)
            )
            if warning_title:
                story.append(
                    Paragraph(
                        f"<b>{escape(warning_title.replace('_', ' ').title())}</b>",
                        styles["BodyText"],
                    )
                )
            if warning.get("description"):
                story.append(
                    Paragraph(
                        escape(str(warning["description"])),
                        styles["BodyText"],
                    )
                )
            phrases = warning.get("matched_phrases") or []
            if phrases:
                story.append(
                    Paragraph(
                        f"Matched wording: {escape(', '.join(map(str, phrases)))}",
                        styles["BodyText"],
                    )
                )
    else:
        story.append(
            Paragraph(
                "Listing description or landlord/chat text was not supplied or extracted; this check was not performed.",
                styles["BodyText"],
            )
        )

    # Alternative area suggestions: neighbourhood names only, not specific flats.
    alternatives = report_data.get("alternative_areas") or {}
    suggested_areas = alternatives.get("areas") or []
    if suggested_areas:
        story.append(
            Paragraph("Nearby neighbourhoods to explore", styles["SectionHeading"])
        )
        if alternatives.get("message"):
            story.append(
                Paragraph(escape(str(alternatives["message"])), styles["BodyText"])
            )
        for alternative in suggested_areas:
            if not isinstance(alternative, dict):
                continue
            name = escape(str(alternative.get("area_name", "Neighbourhood")))
            level_text = escape(str(alternative.get("relative_activity_level", "Relative activity unavailable")))
            distance = alternative.get("distance_from_listing_km")
            distance_text = f" · approximately {float(distance):.1f} km away" if distance is not None else ""
            story.append(
                Paragraph(
                    f"<b>{name}</b> — {level_text} relative incident activity{escape(distance_text)}",
                    styles["BodyText"],
                )
            )
            points_count = alternative.get("address_points_in_dataset")
            if points_count is not None:
                story.append(
                    Paragraph(
                        f"Property-address data points represented: {int(points_count):,}.",
                        styles["BodyText"],
                    )
                )
            if alternative.get("target_monthly_rent_cad") is not None:
                story.append(
                    Paragraph(
                        f"Search target: around CAD ${float(alternative['target_monthly_rent_cad']):,.0f}/month (the rent entered on the form).",
                        styles["BodyText"],
                    )
                )
            if alternative.get("rent_note"):
                story.append(
                    Paragraph(escape(str(alternative["rent_note"])), styles["BodyText"])
                )
            story.append(Spacer(1, 4))
    elif int(report_data.get("risk_score") or 0) >= 25:
        story.append(
            Paragraph("Alternative neighbourhood suggestions", styles["SectionHeading"])
        )
        story.append(
            Paragraph(
                escape(str(alternatives.get("message", "No alternative neighbourhoods were available from the current comparison data."))),
                styles["BodyText"],
            )
        )

    # User actions.
    actions = report_data.get("recommended_actions") or []
    if actions:
        story.append(
            Paragraph("Suggested next steps", styles["SectionHeading"])
        )
        for action in actions:
            story.append(Paragraph(f"• {escape(str(action))}", styles["BodyText"]))

    limitations = report_data.get("limitations", [])

    story.append(
        Paragraph("Information limitations", styles["SectionHeading"])
    )

    if limitations:
        for item in limitations:
            story.append(
                Paragraph(
                    f"• {escape(str(item))}",
                    styles["BodyText"],
                )
            )
    else:
        story.append(
            Paragraph(
                "No additional limitations recorded.",
                styles["BodyText"],
            )
        )

    story.append(
        Paragraph("Important disclaimer", styles["SectionHeading"])
    )

    disclaimer = report_data.get(
        "disclaimer",
        "This report summarizes available records and configured warning signals. "
        "It does not prove ownership, safety, fraud or listing legitimacy.",
    )

    story.append(
        Paragraph(escape(str(disclaimer)), styles["BodyText"])
    )

    story.append(Spacer(1, 12))

    story.append(
        Paragraph(
            "RentalShield provides preliminary screening information only. "
            "Verify important claims independently before paying money or signing a lease.",
            styles["Italic"],
        )
    )

    document.build(story)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"rentalshield_report_{saved_report.id}.pdf",
    )


# --------------------------------------------------
# DELETE A SAVED REPORT
# --------------------------------------------------

@app.post("/reports/<int:report_id>/delete")
@login_required
def delete_report(report_id):
    saved_report = db.session.get(VerificationReport, report_id)

    if saved_report is None or saved_report.user_id != current_user.id:
        abort(404)

    db.session.delete(saved_report)
    db.session.commit()

    flash("Report deleted successfully.", "success")
    return redirect(url_for("dashboard"))


# --------------------------------------------------
# ERROR HANDLERS
# --------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    flash("The submitted content is too large. Please shorten it.", "error")
    return redirect(url_for("home")), 413


# --------------------------------------------------
# --------------------------------------------------
# RENTALSHIELD ANALYTICS
# --------------------------------------------------

@app.get("/analytics")
def analytics():
    """Show source-backed rental market and neighbourhood context."""
    return render_template(
        "analytics.html",
        **build_analytics_context(BASE_DIR, request.args),
    )


# HEALTH CHECK
# --------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "application": "RentalShield",
    }


# --------------------------------------------------
# DATABASE INITIALIZATION
# --------------------------------------------------

with app.app_context():
    db.create_all()


# --------------------------------------------------
# APPLICATION ENTRY POINT
# --------------------------------------------------

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")