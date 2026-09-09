"""
Deterministic, rule-based stand-in for a real LLM extraction call.

This is intentionally NOT hardcoded to one fixture lease: it looks for
"Label: value" lines - the shape virtually every lease template uses -
and omits a field entirely rather than guessing when it finds no
confident match.

Two properties matter more than coverage here, because the whole product
is sold on a checkable record:

1. **Labels are anchored, not searched for as substrings.** An earlier
   version took the first line *containing* a keyword anywhere, which is
   how `Deposit Terms are set out in Schedule 2.` became
   `deposit_amount = 2.0`, and how `Terms and Conditions apply over 999
   months` became a 999-month term. A match now has to be the *label
   half* of a labelled line, and unlabelled prose is simply not
   extracted.

2. **Confidence describes match quality, not the field.** It used to be
   a hardcoded constant per field, so a clean parse and a nonsense one
   both reported 0.75 - a number that never varies carries no
   information. It is now derived: how exactly the label matched, and
   how unambiguous the value was (a bare `05/12/2025` is day-first *by
   assumption*, and says so by scoring lower).

Signatures get the same treatment. Whether a party signed used to be a
whole-document keyword check (`"tenant" in text and "signature" in
text`), which could never tell the two parties apart and could never
say "I don't know" - so a lease stating in plain English that the tenant
had not signed still reported both parties signed, and R5 (severity:
high) returned PASS. Detection is now per party, and a blank signature
line is reported as *unknown* (the field is omitted, so the rule engine
returns NOT_DETERMINABLE) rather than as a signature, because plain text
cannot show ink. Only an explicit statement that a party did not sign
produces a False.

Swapping this for app/ai/anthropic_lease_extractor.py or
openai_lease_extractor.py (a real model call) does not require changing
any code outside app/ai/factory.py, because both implement the same
LeaseExtractor interface.
"""
import re
from datetime import datetime

from app.ai.base import ExtractedField

# Recorded on every field this extractor produces
# (ExtractedField.extracted_by) so a stored confidence can be read for
# what it is: a label-match score, not a model's self-assessment.
EXTRACTOR_ID = "mock-labels"

# A labelled line: "Monthly Rent: QAR 9,000". Only a colon counts as the
# separator - a dash is far too common inside ordinary prose and inside
# values themselves ("Marina Crest - Tower B") to treat as a label
# boundary. The label half is length-capped so a sentence that merely
# happens to contain a colon isn't mistaken for a field label.
_LABELLED_LINE = re.compile(r"^\s*([^:]{1,60}?)\s*:\s*(.*?)\s*$")

_CURRENCY = r"(?:QAR|AED|SAR|USD|EUR|GBP|KWD|BHD|OMR|\$|£|€)"
_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")

DATE_PATTERNS = [
    r"\b(\d{4}-\d{2}-\d{2})\b",
    r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
    r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",  # "1 January 2025"
]

# Words that mean "the value is defined somewhere else", so a number on
# the same line is a cross-reference, not an amount.
_CROSS_REFERENCE = re.compile(
    r"\b(schedule|annex|annexure|appendix|exhibit|clause|section|article|part)\b", re.I
)


def _label_score(label: str, keyword: str) -> float | None:
    """How well `label` (the left half of a labelled line) matches
    `keyword`, or None if it doesn't match at all.

    Scored rather than boolean so that the *best* label on the page
    wins instead of whichever one appeared first: "Rent" and "Monthly
    Rent" both match the monthly-rent keyword, and the exact one should
    win regardless of line order.
    """
    words = re.findall(r"[a-z]+", label.lower())
    target = keyword.lower().split()
    if not words:
        return None
    n = len(target)
    for i in range(len(words) - n + 1):
        if words[i:i + n] != target:
            continue
        if words == target:
            return 0.95          # label is exactly the keyword
        if len(words) <= 3:
            return 0.85          # "security deposit", "monthly rent"
        if len(words) <= 6:
            return 0.65          # keyword buried in a longer label
        return None              # a whole sentence, not a label
    return None


def _find_labelled(
    text: str, *keywords: str, exclude: tuple[str, ...] = ()
) -> tuple[float, int, str, str] | None:
    """Best (score, line_index, label, value) across every labelled line
    and every keyword.

    Ranking is (label score, keyword specificity, earliest line), so a
    two-word keyword matching "Monthly Rent" beats the bare "rent"
    matching a "Rent:" line higher up the page. Only the label score is
    reported as confidence - specificity decides *which* line wins, not
    how sure we are about it.

    `exclude` disqualifies a label outright: the monthly-rent search has
    to accept a bare "Rent:" label, which would otherwise happily match
    "Annual Rent:" in a document that never states a monthly figure and
    report the annual amount as the monthly one.
    """
    best_rank: tuple[float, int, int] | None = None
    best: tuple[float, int, str, str] | None = None
    for i, raw in enumerate(text.splitlines()):
        m = _LABELLED_LINE.match(raw)
        if not m:
            continue
        label, value = m.group(1), m.group(2)
        label_words = set(re.findall(r"[a-z]+", label.lower()))
        if any(word in label_words for word in exclude):
            continue
        for keyword in keywords:
            score = _label_score(label, keyword)
            if score is None:
                continue
            rank = (score, len(keyword.split()), -i)
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best = (score, i, label.strip(), value.strip())
    return best


def _parse_date(raw: str) -> tuple[datetime, bool] | None:
    """Returns (parsed, is_ambiguous). Ambiguous means the written form
    could equally be D/M/Y or M/D/Y - we assume day-first, and the
    caller lowers confidence to say so instead of pretending certainty.
    """
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        numeric_dmy = fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")
        return parsed, bool(numeric_dmy and parsed.day <= 12)
    return None


def _extract_date(value: str) -> tuple[str, float] | None:
    """(ISO date, confidence factor) from a value half, or None."""
    for pattern in DATE_PATTERNS:
        m = re.search(pattern, value)
        if not m:
            continue
        parsed = _parse_date(m.group(1))
        if parsed is None:
            continue
        dt, ambiguous = parsed
        return dt.date().isoformat(), 0.7 if ambiguous else 1.0
    return None


def _extract_amount(value: str) -> tuple[float, float] | None:
    """(amount, confidence factor) from a value half, or None when the
    value doesn't actually state one.

    A number alone is not an amount. `set out in Schedule 2` has a
    number in it and states nothing - that exact string is what used to
    become a deposit of 2.0 at confidence 0.75.
    """
    if _CROSS_REFERENCE.search(value):
        return None
    matches = list(_AMOUNT.finditer(value))
    if len(matches) != 1:
        return None                      # nothing, or too ambiguous to pick
    match = matches[0]
    amount = float(match.group(0).replace(",", ""))
    has_currency = bool(re.search(_CURRENCY, value, flags=re.I))
    is_bare = match.group(0) == value.strip()
    if has_currency or is_bare:
        return amount, 1.0
    # A number with words around it but no currency marker and no
    # cross-reference, e.g. "9000 per month" - probably right, not certainly.
    if len(value.split()) <= 4:
        return amount, 0.8
    return None


_SIGNATURE_FILL = re.compile(r"^[\s_\.\-–—x*]*$")
_NEGATED_SIGNATURE = re.compile(r"\b(?:not|never|without|no)\s+(?:been\s+|yet\s+)?(?:sign|signature)", re.I)
_UNSIGNED = re.compile(r"\bunsigned\b", re.I)

_LANDLORD_WORDS = ("landlord", "lessor")
_TENANT_WORDS = ("tenant", "lessee")


def _signature_state(text: str, aliases: tuple[str, ...], other: tuple[str, ...]):
    """(signed, confidence, source_span) for one party, where `signed`
    may be None meaning "cannot be determined from this text".

    Only lines that (a) mention this party, (b) do not also mention the
    other party, and (c) are about signing at all are considered - a
    line like "signed by both Landlord and Tenant below" names both, so
    it says nothing about either one individually and is skipped.
    """
    blank_line: tuple[None, float, str] | None = None
    for i, raw in enumerate(text.splitlines()):
        low = raw.lower()
        if not any(a in low for a in aliases):
            continue
        if "sign" not in low:
            continue
        if any(o in low for o in other):
            continue
        if _NEGATED_SIGNATURE.search(low) or _UNSIGNED.search(low):
            return False, 0.8, f"line {i + 1}"
        m = _LABELLED_LINE.match(raw)
        if m:
            value = m.group(2)
            if _SIGNATURE_FILL.match(value):
                # An empty signature line. In a plain-text rendering this
                # is genuinely unknowable - the ink, if any, didn't
                # survive into the text - so remember it as "unknown"
                # and keep looking for something conclusive.
                blank_line = (None, 0.0, f"line {i + 1}")
                continue
            return True, 0.75, f"line {i + 1}"
    if blank_line:
        return blank_line
    return None, 0.0, None


class MockLeaseExtractor:
    def extract(self, document_text: str) -> dict[str, ExtractedField]:
        fields: dict[str, ExtractedField] = {}

        def put(name: str, value, score: float, line_index: int, factor: float = 1.0) -> None:
            fields[name] = ExtractedField(
                value, f"line {line_index + 1}", round(score * factor, 2), EXTRACTOR_ID
            )

        # --- parties ---------------------------------------------------
        for name, keyword in (("landlord_name", "landlord"), ("tenant_name", "tenant")):
            found = _find_labelled(document_text, keyword, f"{keyword} name")
            if not found:
                continue
            score, idx, _label, value = found
            # A run-on line ("Landlord: Acme Ltd and the Tenant: Bob
            # Smith agree as follows.") leaves a second label inside the
            # value. That is a sentence, not a name - omit it rather
            # than store a whole clause as somebody's name.
            if not value or ":" in value or len(value) > 80:
                continue
            put(name, value, score, idx)

        # --- the unit this lease is for --------------------------------
        found = _find_labelled(document_text, "premises", "unit", "apartment", "property")
        if found:
            score, idx, _label, value = found
            if value:
                put("unit_reference_text", value, score, idx)

        # --- dates -----------------------------------------------------
        for name, keywords in (
            ("commencement_date", ("commencement date", "commencement", "start date", "lease start")),
            ("expiry_date", ("expiry date", "expiry", "end date", "lease end", "termination date")),
        ):
            found = _find_labelled(document_text, *keywords)
            if not found:
                continue
            score, idx, _label, value = found
            parsed = _extract_date(value)
            if parsed:
                iso, factor = parsed
                put(name, iso, score, idx, factor)

        # --- term ------------------------------------------------------
        found = _find_labelled(document_text, "term", "lease term", "fixed term", "term length")
        if found:
            score, idx, _label, value = found
            m = re.search(r"(\d+)\s*month", value, flags=re.I)
            if m:
                put("term_months", int(m.group(1)), score, idx)

        # --- money -----------------------------------------------------
        for name, keywords, exclude in (
            ("monthly_rent", ("monthly rent", "rent per month", "rent"),
             ("annual", "yearly", "annum", "total")),
            ("annual_rent", ("annual rent", "yearly rent", "rent per annum"),
             ("monthly", "month")),
            ("deposit_amount", ("security deposit", "deposit amount", "deposit"), ()),
        ):
            found = _find_labelled(document_text, *keywords, exclude=exclude)
            if not found:
                continue
            score, idx, _label, value = found
            parsed = _extract_amount(value)
            # `is not None`, not a truthiness test: a stated rent or
            # deposit of 0 is a real (and rule-relevant) value, and the
            # old `if num:` silently dropped it as falsy.
            if parsed is not None:
                amount, factor = parsed
                put(name, amount, score, idx, factor)

        # --- escalation ------------------------------------------------
        found = _find_labelled(document_text, "escalation", "escalation clause", "rent escalation", "rent increase")
        if found:
            score, idx, _label, value = found
            if value:
                vague = re.search(r"mutually agreed|as agreed|to be agreed|as determined|mutually determined", value, flags=re.I)
                defined = re.search(r"\d+\s*%|\bfixed\b|\bannually\b|\bper annum\b|\bcpi\b", value, flags=re.I)
                put("escalation_clause_text", value, score, idx)
                put("escalation_is_defined", bool(defined and not vague), score, idx)

        # --- renewal / termination prose -------------------------------
        found = _find_labelled(document_text, "renewal", "renewal terms", "renewal option")
        if found:
            score, idx, _label, value = found
            if value:
                put("renewal_terms_text", value, score, idx)

        found = _find_labelled(document_text, "termination", "termination terms", "notice period", "early termination")
        if found:
            score, idx, _label, value = found
            # A "Termination Date: 01/03/2026" line is the end date under
            # another name, not a termination *clause* (notice period,
            # conditions, ...). If the value parses as a bare date it's
            # the former.
            if value and _extract_date(value) is None:
                put("termination_terms_text", value, score, idx)

        # --- signatures ------------------------------------------------
        for name, aliases, other in (
            ("landlord_signed", _LANDLORD_WORDS, _TENANT_WORDS),
            ("tenant_signed", _TENANT_WORDS, _LANDLORD_WORDS),
        ):
            signed, confidence, source = _signature_state(document_text, aliases, other)
            # Omitted, not defaulted: a field the extractor can't
            # determine is absent, and the rule engine reads an absent
            # field as NOT_DETERMINABLE rather than as "no".
            if signed is not None:
                fields[name] = ExtractedField(signed, source, confidence, EXTRACTOR_ID)

        return fields
