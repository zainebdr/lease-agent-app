"""
Deterministic, rule-based stand-in for a real LLM extraction call.

This is intentionally NOT hardcoded to one fixture lease: it looks for
common lease phrasing patterns (label: value lines, "Landlord" / "Tenant"
headings, date formats, currency amounts) so it can extract from lease
documents that follow a reasonably standard structure, and simply omits
a field (rather than guessing) when it finds no confident match.

Swapping this for app/ai/llm_lease_extractor.py (a real model call) does
not require changing any code outside app/ai/factory.py, because both
implement the same LeaseExtractor interface.
"""
import re
from datetime import datetime

from app.ai.base import ExtractedField

DATE_PATTERNS = [
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
    r"\b(\d{4}-\d{2}-\d{2})\b",
    r"\b(\d{1,2}\s+\w+\s+\d{4})\b",  # "1 January 2025"
]


def _find_line(text: str, *labels: str) -> tuple[str, int] | None:
    """Find the first line containing any of the given labels (case-insensitive),
    return (line, line_index) or None."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        low = line.lower()
        if any(label.lower() in low for label in labels):
            return line.strip(), i
    return None


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _extract_number(line: str) -> float | None:
    match = re.search(r"[\d,]+(?:\.\d+)?", line)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def _extract_date_from_line(line: str) -> str | None:
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, line)
        if m:
            return m.group(1)
    return None


class MockLeaseExtractor:
    def extract(self, document_text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}

        found = _find_line(document_text, "landlord")
        if found:
            line, idx = found
            name = re.split(r"landlord[:\-]?\s*", line, flags=re.I)[-1].strip()
            if name:
                fields["landlord_name"] = ExtractedField(name, f"line {idx+1}", 0.7)

        found = _find_line(document_text, "tenant")
        if found:
            line, idx = found
            name = re.split(r"tenant[:\-]?\s*", line, flags=re.I)[-1].strip()
            if name:
                fields["tenant_name"] = ExtractedField(name, f"line {idx+1}", 0.7)

        found = _find_line(document_text, "unit", "apartment", "premises")
        if found:
            line, idx = found
            fields["unit_reference_text"] = ExtractedField(line, f"line {idx+1}", 0.6)

        found = _find_line(document_text, "commencement", "start date", "lease start")
        if found:
            line, idx = found
            raw_date = _extract_date_from_line(line)
            parsed = _parse_date(raw_date) if raw_date else None
            if parsed:
                fields["commencement_date"] = ExtractedField(
                    parsed.date().isoformat(), f"line {idx+1}", 0.75
                )

        found = _find_line(document_text, "expiry", "end date", "termination date", "lease end")
        if found:
            line, idx = found
            raw_date = _extract_date_from_line(line)
            parsed = _parse_date(raw_date) if raw_date else None
            if parsed:
                fields["expiry_date"] = ExtractedField(
                    parsed.date().isoformat(), f"line {idx+1}", 0.75
                )

        found = _find_line(document_text, "term")
        if found:
            line, idx = found
            m = re.search(r"(\d+)\s*month", line, flags=re.I)
            if m:
                fields["term_months"] = ExtractedField(int(m.group(1)), f"line {idx+1}", 0.7)

        found = _find_line(document_text, "monthly rent", "rent per month", "rent:")
        if found:
            line, idx = found
            num = _extract_number(line)
            if num:
                fields["monthly_rent"] = ExtractedField(num, f"line {idx+1}", 0.75)

        found = _find_line(document_text, "annual rent", "yearly rent")
        if found:
            line, idx = found
            num = _extract_number(line)
            if num:
                fields["annual_rent"] = ExtractedField(num, f"line {idx+1}", 0.7)

        found = _find_line(document_text, "deposit")
        if found:
            line, idx = found
            num = _extract_number(line)
            if num:
                fields["deposit_amount"] = ExtractedField(num, f"line {idx+1}", 0.75)

        found = _find_line(document_text, "escalation", "rent increase")
        if found:
            line, idx = found
            is_defined = bool(re.search(r"\d+\s*%|\bfixed\b|\bannually\b", line, flags=re.I))
            defined_vague = "mutually agreed" in line.lower() or "as agreed" in line.lower()
            fields["escalation_clause_text"] = ExtractedField(line, f"line {idx+1}", 0.6)
            fields["escalation_is_defined"] = ExtractedField(
                is_defined and not defined_vague, f"line {idx+1}", 0.6
            )

        signed_section = document_text.lower()
        fields["landlord_signed"] = ExtractedField(
            "landlord" in signed_section and "signature" in signed_section, "signature block", 0.5
        )
        fields["tenant_signed"] = ExtractedField(
            "tenant" in signed_section and "signature" in signed_section, "signature block", 0.5
        )

        return fields
