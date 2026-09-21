# Slovak Parcel Service Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-slovak-parcel-service.svg)](https://github.com/ha-parcel-integrations/ha-slovak-parcel-service/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-slovak-parcel-service/total.svg)](https://github.com/ha-parcel-integrations/ha-slovak-parcel-service/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks Slovak Parcel Service packages.
No account is needed: add the tracking numbers you want to follow.

Part of the [ha-parcel-integrations](https://ha-parcel-integrations.github.io/) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

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

- Track any number of Slovak Parcel Service parcels by tracking code — no account needed
- Multiple hubs, one per delivery postcode, for tracking parcels sent to more than one address (e.g. home + work)
- Per-parcel sensor with canonical status, carrier status text, sender, weight and an official tracking deep-link
- Recipient name shown once your delivery postcode is set (confirms you're the addressee)
- Summary sensors: incoming parcels, next delivery, recently delivered parcels
- Read-only **Deliveries** calendar with the expected delivery windows
- `slovak_parcel_service.track_parcel` / `slovak_parcel_service.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered, delivery time changed)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- A Slovak Parcel Service parcel and its tracking code (from the shipping
  confirmation email or the missed-delivery card) — no account needed

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-slovak-parcel-service` as an **Integration**.
3. Install **Slovak Parcel Service** and restart Home Assistant.

### Manual

Copy `custom_components/slovak_parcel_service` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Slovak Parcel Service**. No account is needed — you're only asked for your delivery postcode, which confirms you're the recipient so a parcel's addressee name can be shown. The postcode identifies the hub, so if you track parcels to more than one address, add the integration again with the other postcode — each becomes its own hub, with its own device and sensors. A postcode can only belong to one hub at a time; to change it, remove the hub and add it again with the new postcode.

Then add parcels via the integration's **Configure** dialog, the [`slovak_parcel_service.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Settings | Delivered parcels: filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Settings | Include status history | off | Adds a `history` attribute per parcel with each status update. |

The delivery postcode itself is set once at setup and isn't editable afterwards — it's what identifies the hub. Add a second hub for a different postcode instead of trying to change this one's.

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery, stopped entirely once nothing is left to track) with nothing to
configure. See [CLAUDE.md](CLAUDE.md) for the details.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Slovak Parcel Service → ⋮ → Delete**. Nothing is stored on Slovak Parcel Service's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.slovak_parcel_service_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.slovak_parcel_service_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.slovak_parcel_service_next_delivery` | Earliest expected delivery moment across all active parcels |
| `sensor.slovak_parcel_service_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.slovak_parcel_service_last_successful_update` | Diagnostic: when Slovak Parcel Service was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced / received by Slovak Parcel Service |
| `out_for_delivery` | With the courier today |
| `delivered` | Delivered |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Slovak Parcel Service device):

| Event | When |
|---|---|
| `slovak_parcel_service_parcel_registered` | A new parcel appears in the active list |
| `slovak_parcel_service_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `slovak_parcel_service_parcel_delivered` | A parcel is delivered |
| `slovak_parcel_service_parcel_delivery_time_changed` | The expected delivery window changes |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `slovak_parcel_service.track_parcel` | `tracking_code`, `postal_code` (optional) | Start tracking a parcel. `postal_code` picks the hub when more than one is set up; omit it with a single hub. |
| `slovak_parcel_service.untrack_parcel` | `tracking_code` | Stop tracking a parcel, on whichever hub(s) track it |

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
    custom_components.slovak_parcel_service: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Slovak Parcel Service has not scanned it yet (their API answers `not_found` until the first scan), or the code is wrong. It will pick up automatically once scanned.
- **A status logs "Unrecognised Slovak Parcel Service status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-slovak-parcel-service/issues/new) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Slovak Parcel Service, Home Assistant, or any other third party referenced in this project. Please don't contact Slovak Parcel Service for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Slovak Parcel Service's terms. Data is sent only to Slovak Parcel Service's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the same public tracking endpoint as the Slovak Parcel Service consumer website. Its fixed website transport key can be rotated by the carrier; an authorization failure stops updates and must be reported to the maintainers.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
