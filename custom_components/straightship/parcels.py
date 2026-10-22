"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`. Everything else — the timestamp parsing, the history
builder, the sort contract, the delivered filter, the one-shot warning for
unmapped statuses — is suite-wide machinery and should be left alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any
from urllib.parse import quote

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-straightship/issues/new"
    "?template=unrecognised_status.yml"
)

# Keys are the status sentences after :func:`normalize_status_text`. Exact
# text only: a sentence that is not listed falls back to the newest mappable
# history event rather than being guessed at.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "Package information is created in the system": ParcelStatus.REGISTERED,
    "Package Received with Origin Scan warehouse": ParcelStatus.IN_TRANSIT,
    "Package is received at a Straightship station": ParcelStatus.IN_TRANSIT,
    "Package is in transit to a Straightship station": ParcelStatus.IN_TRANSIT,
    "Loading": ParcelStatus.IN_TRANSIT,
    "Package will be delivered before 9:00pm": ParcelStatus.OUT_FOR_DELIVERY,
    (
        "An attempt was made to deliver your package-unable to leave package at "
        "safe location (please e-mail <address> to make arrangement)"
    ): ParcelStatus.PROBLEM,
    "Delivered": ParcelStatus.DELIVERED,
}

# The location the carrier appends to a sentence: "(City, PR)".
_LOCATION_SUFFIX_RE = re.compile(r"\s*\([^()]*,\s*[A-Za-z]{2}\)\s*$")
# The failed-attempt sentence carries a contact address, plain or obfuscated.
_CONTACT_RE = re.compile(r"(please e-mail ).+?( to make arrangement)")

# Categories we have already warned about, so each is logged only once per HA
# session instead of on every poll. Only the category is ever logged: the
# status text itself can sit next to a user's location.
_unmapped_statuses_logged: set[str] = set()


def normalize_status_text(text: str | None) -> str:
    """Return a status sentence without its location suffix or contact address."""
    collapsed = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()
    collapsed = _LOCATION_SUFFIX_RE.sub("", collapsed)
    return _CONTACT_RE.sub(r"\1<address>\2", collapsed)


def _warn_unmapped_status(category: str, reported: str = "unknown") -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if category in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(category)
    _LOGGER.warning(
        "Unrecognised Straightship %s status — help us map it. Open an issue "
        "and paste this line: %s\n  category=%s → reported as '%s'; the "
        "status text itself is in the integration diagnostics",
        category,
        NEW_ISSUE_URL,
        category,
        reported,
    )


def map_event_status(text: str | None) -> ParcelStatus | None:
    """Map one status sentence to a canonical status, or ``None``.

    Never warns: whether a gap matters depends on the caller, which owns the
    one-shot warning.
    """
    if not text:
        return None
    return _STATUS_MAP.get(normalize_status_text(text))


def map_parcel_status(
    summary: str | None, history_texts: list[str] | None = None
) -> ParcelStatus:
    """Map the summary status, else the newest mappable history event.

    An absent summary (a parcel the carrier does not know yet) reports
    ``unknown`` silently. An unmapped summary always warns once, naming the
    category and what it reported instead — the summary words of a parcel in
    flight have never been seen, so a silent history fallback would hide the
    one case we still need reported.
    """
    if not summary:
        return ParcelStatus.UNKNOWN
    mapped = map_event_status(summary)
    if mapped is not None:
        return mapped
    for earlier in reversed(history_texts or []):
        fallback = map_event_status(earlier)
        if fallback is not None:
            _warn_unmapped_status("summary", fallback.value)
            return fallback
    _warn_unmapped_status("summary")
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 UTC string for the reply's ``YYYY-MM-DD HH:MM:SS``.

    The carrier states its timestamps are UTC. Anything that does not parse is
    ``None``.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC).isoformat()


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def build_history(
    events: list | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the reply's history rows.

    Each entry is ``{timestamp, status, raw_status}``, top-level (not under
    ``raw``) so it survives the aggregator's ``strip_raw()``. ``raw_status`` is
    the sentence without its location suffix, so the list carries no place
    names. Oldest to newest; the sort is stable so rows sharing a timestamp keep
    document order. Capped to the most recent ``max_events``.
    """
    entries: list[dict] = []
    unmapped = False
    for event in events or []:
        if not isinstance(event, dict):
            continue
        timestamp = to_iso_timestamp(event.get("timestamp"))
        if not timestamp:
            continue
        text = normalize_status_text(event.get("status"))
        status = map_event_status(text)
        unmapped = unmapped or (bool(text) and status is None)
        entries.append(
            {"timestamp": timestamp, "status": status, "raw_status": text or None}
        )
    if unmapped:
        _warn_unmapped_status("history")
    # Fixed-width UTC strings sort chronologically; the sort is stable, so rows
    # sharing a second stay in document order.
    entries.sort(key=lambda entry: entry["timestamp"])
    return entries[-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=quote(tracking_code, safe=""))


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order. A key is ``None`` when the
    carrier does not expose it — never omitted. ``delivered_at`` comes from the
    delivered history event alone; the carrier's own "last updated" repeats it.
    """
    tracking_code = raw.get("tracking_code")
    events = [e for e in raw.get("history") or [] if isinstance(e, dict)]
    summary = raw.get("package_status")
    status = map_parcel_status(summary, [e.get("status") for e in events])
    delivered = status is ParcelStatus.DELIVERED

    delivered_at = None
    if delivered:
        for event in reversed(events):
            if map_event_status(event.get("status")) is ParcelStatus.DELIVERED:
                delivered_at = to_iso_timestamp(event.get("timestamp"))
                break

    return {
        "carrier": "Straightship",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": summary or None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": False,
        "pickup_point": None,
        "url": tracking_url(tracking_code),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
