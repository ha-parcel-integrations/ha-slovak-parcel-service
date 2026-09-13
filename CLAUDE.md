# Working in this repository

Home Assistant custom integration for **Slovak Parcel Service** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

API mechanics — endpoints, parameters, status vocabularies — live in the
private `carrier-research/slovak-parcel-service/api/` and are **never** copied here.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **If this carrier can reach `ParcelStatus.AT_PICKUP_POINT` from a real raw
  status/code**, it needs an `awaiting_pickup` sensor — see *Parcel contract*
  in `CONVENTIONS.md`. Say "pickup point", not "ServicePoint"/"parcel
  shop"/"locker", for the generic concept. `ha-dhl-nl`, `ha-dpd`, `ha-gls`,
  `ha-inpost` are reference implementations; `slovak_parcel_service` here does not
  demonstrate it yet.

## Carrier-specific notes

**API mechanics live in `carrier-research/slovak-parcel-service/api/slovak-parcel-service/`** (private research
repo) — the endpoint, request/response shape, the numeric status vocabulary
and its `ParcelStatus` derivation, and the timestamp format. Not duplicated
here; this section is integration-level decisions only.

**`API_KEY` in `const.py` is a shared secret, not a user credential — shipped
under an explicit maintainer exception to the suite's standing shared-secret
refusal** (see `carrier-research/slovak-parcel-service/slovak-parcel-service.md`, Log 2026-09-01). It is the fixed
`Api-Key` value MySPS's own public web bundle sends for every visitor, not
something a Home Assistant user could obtain themselves. Treat its rotation
or revocation as a first-class integration failure, never as a reason to fall
back to scraping the legacy HTML tracker: `api.py` raises a distinct error on
401 so the coordinator can warn that the approved key may have rotated,
rather than treating it as a normal auth failure. The key must never appear
in diagnostics, fixtures, logs or documentation beyond this pointer.

**`sender`, `weight` and `receiver` are filled from a second MySPS endpoint,
`getShipmentInfo` — a deliberate exception to "the tracking payload alone
decides what's populated."** `sender`/`weight` come back on that endpoint's
`panel_0` unconditionally; `receiver` only appears once the request's
`recipientzip` header matches the shipment's real one (`data.authorized`
flips true and `panel_1` appears) — a wrong or absent postcode is not an
error, it's the endpoint's normal unauthenticated shape, so it must never
fail the parcel. This is why the hub now asks for the delivery postcode at
setup (`CONF_POSTAL_CODE`, editable in Settings) — unlike the tracking
endpoint, `getShipmentInfo` does not hand out the recipient's name to anyone
holding the shared key alone, so the earlier "would turn the tracking code
into a PII dereference key" objection does not apply to `receiver` here. Do
**not** persist anything else this endpoint returns (phone numbers,
e-mail addresses, street addresses, COD, insurance, services, ref numbers) —
they have no canonical home; `diagnostics.TO_REDACT` still redacts all of it
regardless. `sender`/`weight`/`receiver` are fetched **once per tracking
code's lifetime**, not every poll — see `coordinator._info_cache` — since
they're static shipment attributes; a failed fetch is retried next cycle
rather than treated as fatal, and a 429 on it still feeds the same
`_consecutive_429` backoff as the tracking call, since both endpoints share
one rate limit. `dimensions`, `planned_from`, `planned_to` and `pickup_point`
are still always `None` — this route has no confirmed field for any of them.
`const.CAPABILITIES` (`weight`, `url`, `history`) must stay in agreement with
whichever of these fields actually come back non-null.

**Status code `50` (`Vrátený do skladu`) maps to `in_transit`, never to a
terminal `returning`.** Both confirmed histories showed `50` followed by a
later out-for-delivery event, so the parcel is back in the carrier's network
awaiting another attempt, not on its way back to the sender. Do not add a
`returning` mapping for `50` without new evidence.

**Code `38` (`DDSP`, `Upresnenie doručenia (cez WEB)`) is recognised but has
no canonical equivalent** — it records the recipient giving delivery
instructions on the website, which is not a movement of the parcel. It is
listed in `_NON_LIFECYCLE_EVENT_CODES` so its history entry keeps
`status: null` *without* the one-shot "report this status" warning: the
warning exists to surface codes nobody has looked at yet, and asking users to
report one we have already judged is noise. Add a code there only once its
meaning is confirmed and genuinely maps to nothing.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Parcels` and
`Settings`. `Parcels` is one editable multi-code list; `Settings` is
a flat form — some carriers use one sectioned form
(`data_entry_flow.section`) instead; both are generator variants, not carrier
decisions. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default) apply changes live: an update listener
  calls `async_request_refresh()`, so added/removed parcel sensors appear
  immediately (this is also the resume path after polling has fully
  suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Tracking-code validation

`valid_tracking_code` in `config_flow.py` accepts every non-empty code — no
format regex. This is a suite-wide convention, not a per-carrier TODO: real
tracking-number formats vary too much across carriers, and are often not
fully confirmed even for this one, to gate on a guessed shape. A too-strict
regex risks rejecting a genuinely valid code; an actually-bad code just comes
back "not found" on the next poll, which is a far cheaper failure mode. Do
not add one back in, even once the format is confirmed.

## Multi-hub: one hub per delivery postcode

Not a single-instance integration. Each config entry is a "hub" scoped to
one delivery postcode (`CONF_POSTAL_CODE`), asked once at setup and used as
the `unique_id` — no country prefix, since this carrier only ever serves
Slovakia, so a normalized postcode is already unambiguous on its own.
`config_flow.async_step_user` calls `async_set_unique_id` +
`_abort_if_unique_id_configured()` so the same postcode cannot be added
twice, while a different postcode creates a second, independent hub (e.g.
home + work) — each with its own device, entities and tracked-parcel list.
Adding a parcel to a hub needs only its tracking number; `CONF_POSTAL_CODE`
is what authorises the receiver's name on that hub's shipment-info calls.

The postcode is fixed at setup, not editable afterwards: it is the hub's
identity key, and an identity key must not change under a configured entry
— doing so could silently turn one hub into a different one, or collide
with a postcode another hub already owns. Getting a different postcode
means adding a new hub, not editing this one; the options `Settings` step
does not offer it at all.

Slovak postcodes have a confirmed, fixed 5-digit shape (`^\d{5}$`, after
stripping the conventional "NNN NN" space), so — unlike tracking codes —
`config_flow.valid_postcode` *does* gate on a regex; this does not
contradict the "no format regex" tracking-code rule below, which is about
carrier-issued identifiers whose shape isn't reliably known, not a national
postcode standard.

Services are process-wide, not per-hub: `async_setup_services` registers
them once and `async_unload_services` (called from `__init__.py`) only
removes them once the *last* hub unloads, so unloading one hub never breaks
another. `track_parcel` takes an optional `postal_code` field to pick the
target hub when more than one is configured (required only when ambiguous);
`untrack_parcel` needs no such field — it removes the code from whichever
hub(s) track it.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` recompute `update_interval` at the end of every
refresh. `slovak_parcel_service/coordinator.py` is the canonical implementation
every carrier mirrors; the design rationale (quiet window, tiers, stagger,
backoff, delivered-skip) is spelled out below.

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.
- **Delivered codes are skipped from the fetch (account-less carriers only):**
  once a tracking code's payload comes back `delivered`, `coordinator.py`
  excludes it from the next cycle's fetch — its payload can never change
  again. `self._delivered_codes` (keyed on the tracking code, not the barcode)
  is rebuilt from each cycle's results and intersected with the tracked set on
  untrack. The code stays in the options list, keeps its sensor and its
  cached payload, and still shows under the retention window — it just costs
  no more requests. `coordinator.delivered_codes` surfaces the count in
  diagnostics. Account-based carriers have nothing to skip here — one account
  call already returns everything, so their `delivered_codes` is always empty.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (`_STATUS_MAP`, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code + postcode validation) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.slovak_parcel_service
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
