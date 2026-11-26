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

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, tzinfo
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
    summary: str | None, history_texts: list[str | None] | None = None
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
        "text": f"{round(length)} x {round(width)} x {round(height)} cm",
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


def _moment(value: Any, zone: tzinfo) -> tuple[datetime, bool, str] | None:
    """Return ``(aware datetime, date_only, text)`` for a carrier time, or ``None``.

    A naive time or a bare date gets ``zone``; an aware time keeps its offset
    and, when it came in as a string, its own spelling.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        moment = datetime.combine(value, time.min, tzinfo=zone)
        return moment, True, moment.isoformat()
    elif isinstance(value, str) and value:
        try:
            if len(value) == 10:
                moment = datetime.combine(
                    date.fromisoformat(value), time.min, tzinfo=zone
                )
                return moment, True, moment.isoformat()
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed, False, value
    else:
        return None
    moment = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    return moment, False, moment.isoformat()


def _end_of_day(moment: datetime, zone: tzinfo) -> datetime:
    local = moment.astimezone(zone)
    return datetime.combine(local.date(), time(23, 59, 59), tzinfo=zone)


def planned_window(
    start: Any, end: Any, *, zone: tzinfo
) -> tuple[str | None, str | None]:
    """Return ``(planned_from, planned_to)`` in the canonical window shape.

    ``start`` and ``end`` are ISO strings, dates or datetimes, or ``None``; an
    epoch number must be converted first (see ``to_iso_timestamp``). A window
    keeps both ends. One moment with a time ends at ``23:59:59`` the same local
    day; a date covers ``00:00:00`` to ``23:59:59``; two dates run from the
    first day's start to the last day's end. Equal values are one moment,
    reversed values are swapped, and ``planned_to`` is never set without
    ``planned_from``. ``zone`` is the carrier's own zone: it dates naive times
    and bare dates and decides which day a moment ends on, so a ``Z`` moment
    still ends on the carrier's local day. When nothing is left of that day
    after the moment, there is no ``planned_to``.
    """
    first = _moment(start, zone)
    last = _moment(end, zone)
    if first is None:
        first, last = last, None
    if first is None:
        return None, None

    def closing(moment: tuple[datetime, bool, str]) -> datetime:
        return _end_of_day(moment[0], zone) if moment[1] else moment[0]

    if last is not None and closing(last) < first[0]:
        first, last = last, first
    if last is not None and closing(last) == first[0]:
        last = None

    if last is None:
        end_of_day = _end_of_day(first[0], zone)
        return first[2], end_of_day.isoformat() if end_of_day > first[0] else None
    return first[2], closing(last).isoformat() if last[1] else last[2]


_NO_WINDOW_STATUSES = frozenset(
    {ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED}
)


def planned_for_status(
    status: ParcelStatus, planned_from: str | None, planned_to: str | None
) -> tuple[str | None, str | None]:
    """Empty the delivery window once the parcel is at its destination or done."""
    if status in _NO_WINDOW_STATUSES:
        return None, None
    return planned_from, planned_to


def is_pickup(
    route_flag: bool | None,
    status: ParcelStatus,
    event_statuses: Iterable[ParcelStatus | None] = (),
) -> bool:
    """Whether the delivery route ends at a pickup point.

    The carrier's own route flag wins; otherwise the parcel having reached
    ``AT_PICKUP_POINT`` at any point counts, so the value survives collection.
    """
    if route_flag:
        return True
    return status is ParcelStatus.AT_PICKUP_POINT or any(
        event is ParcelStatus.AT_PICKUP_POINT for event in event_statuses
    )


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
    event_statuses = [map_event_status(e.get("status")) for e in events]
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
        "contents": None,
        "status": status,
        "raw_status": summary or None,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None,
        "planned_to": None,
        "pickup": is_pickup(None, status, event_statuses),
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
