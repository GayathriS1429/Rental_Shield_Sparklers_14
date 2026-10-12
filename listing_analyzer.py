"""Rule-based warning phrase checks for RentalShield listing/chat text.

This module identifies configured phrases in either a rental listing
(description) or copied landlord/agent conversation. It is not a sentiment
classifier, fraud detector, or substitute for human review.
"""

from __future__ import annotations

import re
from typing import Any


WARNING_PATTERNS: dict[str, dict[str, Any]] = {
    "deposit_before_viewing": {
        "patterns": [
            # "Send a deposit before viewing the apartment."
            r"\b(?:send|pay|transfer|e-?transfer|provide|submit)\b.{0,40}\b(?:a\s+)?(?:security\s+)?(?:holding\s+)?deposit\b.{0,60}\b(?:before|prior to|without)\b.{0,35}\b(?:viewing|view|tour|visit|seeing|inspecting)\b",
            # "Deposit is required prior to viewing."
            r"\b(?:security\s+)?(?:holding\s+)?deposit\b.{0,60}\b(?:required|requested|needed|paid|payable)\b.{0,40}\b(?:before|prior to|without)\b.{0,35}\b(?:viewing|view|tour|visit|seeing|inspecting)\b",
            # "Before viewing, please send the deposit."
            r"\b(?:before|prior to|without)\b.{0,35}\b(?:viewing|view|tour|visit|seeing|inspecting)\b.{0,60}\b(?:send|pay|transfer|e-?transfer|provide|submit|deposit)\b.{0,40}\b(?:deposit)?\b",
            r"\bpay\b.{0,25}\bdeposit\b.{0,30}\bto view\b",
        ],
        "description": (
            "The text appears to request a deposit before a viewing. "
            "Verify the property and arrange a legitimate viewing before sending money."
        ),
        "severity": "high",
    },
    "payment_within_24_hours": {
        "patterns": [
            # "Pay/send the deposit within 24 hours."
            r"\b(?:pay|send|transfer|e-?transfer|make|submit)\b.{0,55}\b(?:within|in|by)\s+(?:the\s+next\s+)?(?:24|twenty[- ]four)\s*(?:hours?|hrs?)\b",
            # "You have 24 hours to send the money."
            r"\b(?:24|twenty[- ]four)\s*(?:hours?|hrs?)\b.{0,40}\b(?:to pay|to send|to transfer|to e-?transfer|to make (?:the )?payment|to secure|deadline)\b",
            # "Payment is due in the next 24 hours."
            r"\bpayment\b.{0,40}\b(?:due|required|expected)\b.{0,30}\b(?:within|in)\s+(?:the\s+next\s+)?(?:24|twenty[- ]four)\s*(?:hours?|hrs?)\b",
        ],
        "description": (
            "The text sets a 24-hour payment deadline. Pressure to act quickly "
            "is a reason to pause and verify the listing and recipient."
        ),
        "severity": "high",
    },
    "deposit_to_secure_listing": {
        "patterns": [
            r"\b(?:send|pay|transfer|e-?transfer|provide)\b.{0,35}\b(?:a\s+)?(?:security\s+)?(?:holding\s+)?deposit\b.{0,45}\b(?:to|in order to)\s*(?:secure|reserve|hold|lock in)\b",
            r"\b(?:deposit|holding deposit|security deposit)\b.{0,50}\b(?:required|needed|requested)\b.{0,35}\b(?:to|for)\s*(?:secure|reserve|hold|lock in)\b",
            r"\b(?:secure|reserve|hold|lock in)\b.{0,40}\b(?:the\s+)?(?:apartment|unit|room|property|rental|listing)\b.{0,45}\bdeposit\b",
        ],
        "description": (
            "The text requests a deposit to reserve or secure the rental. "
            "Confirm the viewing, landlord identity, payment recipient and written terms before paying."
        ),
        "severity": "medium",
    },
    "urgent_payment_pressure": {
        "patterns": [
            r"\bpay\s+(?:now|immediately|right away|today|tonight)\b",
            r"\b(?:urgent|immediate|instant)\s+payment\s+(?:required|needed|only)\b",
            r"\b(?:payment|deposit|transfer)\s+(?:must|needs to|has to)\s+(?:be\s+)?(?:made|sent|paid)\s+(?:now|today|immediately)\b",
            r"\bfirst come[ -]+first served\b.{0,60}\b(?:pay|payment|deposit|transfer)\b",
            r"\b(?:another|many other|several other) applicants?\b.{0,60}\b(?:pay|payment|deposit|transfer|send money)\b",
        ],
        "description": (
            "The text uses urgent payment or competition pressure. "
            "Do not let a deadline replace normal verification."
        ),
        "severity": "medium",
    },
    "landlord_cannot_show_property": {
        "patterns": [
            r"\b(?:owner|landlord|agent)\b.{0,60}\b(?:overseas|out of the country|abroad)\b",
            r"\b(?:cannot|can't|cant|unable to|not able to)\b.{0,45}\b(?:show|show you|arrange|allow|provide)\b.{0,35}\b(?:the\s+)?(?:property|apartment|unit|room|viewing)\b",
            r"\b(?:keys?|key)\b.{0,50}\b(?:courier|mail|ship|post|deliver)\b",
            r"\b(?:no|not possible to arrange a)\s+(?:in-person\s+)?viewing\b",
            r"\b(?:view|viewing)\b.{0,35}\b(?:after|once)\b.{0,25}\b(?:deposit|payment|transfer)\b",
        ],
        "description": (
            "The text may indicate that a normal viewing or in-person handover "
            "is unavailable. Verify independently before sending money."
        ),
        "severity": "medium",
    },
    "unusual_payment_method": {
        "patterns": [
            r"\b(?:gift cards?|cryptocurrency|crypto|bitcoin|btc)\b.{0,70}\b(?:pay|payment|deposit|rent|send|transfer)\b",
            r"\b(?:pay|payment|deposit|rent|send|transfer)\b.{0,70}\b(?:gift cards?|cryptocurrency|crypto|bitcoin|btc)\b",
            r"\b(?:wire transfer|cash app|western union|moneygram)\b.{0,50}\b(?:deposit|rent|payment|send|pay)\b",
            r"\b(?:deposit|rent|payment|send|pay)\b.{0,50}\b(?:wire transfer|western union|moneygram)\b",
        ],
        "description": (
            "The text mentions a payment method that deserves extra scrutiny. "
            "Verify the recipient and use a traceable, appropriate payment method."
        ),
        "severity": "high",
    },
}


def _clean_text(value: str) -> str:
    """Normalize whitespace but retain the user's original wording."""
    return re.sub(r"\s+", " ", value).strip()


def analyze_listing(description: str) -> dict[str, Any]:
    """Identify configured warning phrases in listing text or copied chat.

    The function name is retained for compatibility with existing code.
    The input may be a rental listing description or copied landlord/agent
    conversation. Findings are phrase matches that should be reviewed in
    context; they are not proof of fraud.
    """
    if not isinstance(description, str) or not description.strip():
        return {
            "status": "invalid_input",
            "message": "Paste a rental listing description or landlord/agent chat to analyze.",
            "warnings": [],
            "warning_count": 0,
            "disclaimer": (
                "This check searches for configured wording patterns. "
                "It is not a sentiment classifier or proof of fraud."
            ),
        }

    text = _clean_text(description)
    warnings: list[dict[str, Any]] = []

    for warning_type, rule in WARNING_PATTERNS.items():
        matched_phrases: list[str] = []
        seen_normalized: set[str] = set()

        for pattern in rule["patterns"]:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                phrase = match.group(0).strip()
                normalized_phrase = phrase.casefold()

                if normalized_phrase not in seen_normalized:
                    seen_normalized.add(normalized_phrase)
                    matched_phrases.append(phrase)

        if matched_phrases:
            warnings.append(
                {
                    "type": warning_type,
                    "description": rule["description"],
                    "severity": rule["severity"],
                    "matched_phrases": matched_phrases,
                }
            )

    return {
        "status": "success",
        "message": (
            "Potential warning signals were found. Review each phrase in context."
            if warnings
            else "No configured warning phrases were detected. This does not establish that the listing is legitimate."
        ),
        "warnings": warnings,
        "warning_count": len(warnings),
        "disclaimer": (
            "This is rule-based text screening for configured warning phrases, "
            "not sentiment analysis and not proof of fraud. Some matches may be "
            "benign or require human interpretation; scammers may also use "
            "different wording that this checker does not detect."
        ),
    }
