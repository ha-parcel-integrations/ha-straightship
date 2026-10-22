# Straightship Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-straightship.svg)](https://github.com/ha-parcel-integrations/ha-straightship/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-straightship/total.svg)](https://github.com/ha-parcel-integrations/ha-straightship/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your [Straightship](https://straightship.com) parcels (Canada B2C deliveries). No account is needed — you enter the tracking code yourself, just like on the Straightship website.

> **Pre-1.0.** Built from three delivered parcels. Straightship has no documented API and its overall status has only been seen reading `Delivered`, so for a parcel still on its way the integration reads the newest recognised entry of the status history instead and logs one warning about it. If neither can be mapped the parcel shows `unknown` and one warning is logged. A warning names the kind of status (summary or history) and what was reported instead, never the status text. Please [open an issue](https://github.com/ha-parcel-integrations/ha-straightship/issues/new?template=unrecognised_status.yml) when you see it.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Straightship parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `out_for_delivery` / `delivered` / …), the carrier's own status text and a tracking deep-link
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- `straightship.track_parcel` / `straightship.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- A Straightship parcel and its tracking code (from the shipping
  confirmation email) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-straightship` as an **Integration**.
3. Install **Straightship** and restart Home Assistant if HACS asks you to.

Or click the button below to open it directly in HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-straightship&category=integration)

### Manual

Copy `custom_components/straightship` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=straightship)

Or set it up by hand: add the integration via **Settings → Devices & Services → Add Integration → Straightship**. There is nothing to fill in: the hub is created immediately (Straightship tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`straightship.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email; add it with or without spaces, any capitalisation.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Straightship
  hub out there polls at exactly the same second.

Straightship publishes no delivery window, so a parcel that is out for delivery is always in the 15-minute tier until it is delivered.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Straightship → ⋮ → Delete**. Nothing is stored on Straightship's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.straightship_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.straightship_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.straightship_next_delivery` | Earliest expected delivery moment across all active parcels (stays empty: Straightship publishes no delivery window) |
| `sensor.straightship_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.straightship_last_successful_update` | Diagnostic: when Straightship was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Straightship has created the package in its system |
| `in_transit` | Scanned at origin, received at or heading to a Straightship station, or loading |
| `out_for_delivery` | Promised for delivery today |
| `delivered` | Delivered |
| `problem` | A delivery attempt failed (for example, no safe place to leave the parcel); a new attempt normally follows |
| `unknown` | Not yet on Straightship's system, or a status we have not mapped yet |

`at_pickup_point` and `returning` never occur: Straightship reports neither.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Straightship device):

| Event | When |
|---|---|
| `straightship_parcel_registered` | A new parcel appears in the active list |
| `straightship_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `straightship_parcel_delivered` | A parcel is delivered |
| `straightship_parcel_delivery_time_changed` | The expected delivery window changes — Straightship gives no delivery window, so this does not fire today |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `straightship.track_parcel` | `tracking_code` | Start tracking a parcel |
| `straightship.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.straightship: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Straightship does not know the code yet, or the code is wrong. It picks up automatically once the parcel is in their system.
- **A status logs "Unrecognised Straightship summary status" or "…history status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-straightship/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended; the text itself is in the integration's diagnostics.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Straightship, Home Assistant, or any other third party referenced in this project. Please don't contact Straightship for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Straightship's terms. Data is sent only to Straightship's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration reads the same public tracking lookup that Straightship offers for each tracking code. It never uses the proof-of-delivery lookup.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
