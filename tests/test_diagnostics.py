"""Tests for Straightship diagnostics."""
from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.straightship.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "STRTD0000000000001"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "STRTD0000000000001",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "delivered_at": "2026-01-05T15:04:05+00:00",
            "history": [{"timestamp": "2026-01-05T15:04:05+00:00", "status": None}],
            "raw": {
                "tracking_code": "STRTD0000000000001",
                "client_code": "CLIENT1",
                "package_id": "PKG1",
                "last_updated": "2026-01-05 15:04:05",
                "package_status": "Delivered",
                "history": [
                    {
                        "timestamp": "2026-01-05 15:04:05",
                        "status": "Delivered (Testville, ON)",
                        "city": "Testville",
                    }
                ],
                "html": "<html>Testville</html>",
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    raw = result["incoming"][0]["raw"]
    for key in ("tracking_code", "client_code", "package_id", "last_updated", "html"):
        assert raw[key] == "**REDACTED**"
    assert raw["history"][0]["timestamp"] == "**REDACTED**"
    assert raw["history"][0]["city"] == "**REDACTED**"
    assert result["incoming"][0]["delivered_at"] == "**REDACTED**"
    assert result["incoming"][0]["history"][0]["timestamp"] == "**REDACTED**"
    # the status sentence repeats the city inline; that must not survive either
    assert raw["history"][0]["status"] == "Delivered"
    assert "Testville" not in str(result)
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
