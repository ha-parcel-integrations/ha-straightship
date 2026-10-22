"""Diagnostics support for the Straightship parcel tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import StraightshipConfigEntry
from .parcels import normalize_status_text

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    # carrier payload fields
    "recipient",
    "deliveryAddress",
    "address",
    "postalCode",
    "postal_code",
    "city",
    "street",
    "email",
    "name",
    "driver",
    "signature",
    "tracking_id",
    "client_code",
    "package_id",
    "last_updated",
    "timestamp",
    "delivered_at",
    "location",
    "recipient_name",
    "order_name",
    "pod_url",
    "pod",
    "media",
    "html",
}


def _redact_parcels(parcels: list[dict]) -> list[dict]:
    """Redact by key, then drop the location the status sentences carry inline."""
    redacted = async_redact_data(parcels, TO_REDACT)
    for parcel in redacted:
        raw = parcel.get("raw")
        rows = raw.get("history") if isinstance(raw, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("status"), str):
                row["status"] = normalize_status_text(row["status"])
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StraightshipConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Straightship config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": _redact_parcels(coordinator.data or []),
        "delivered": _redact_parcels(coordinator.delivered or []),
    }
