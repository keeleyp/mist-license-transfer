# Mist License Transfer

A simple command-line tool for moving Juniper Mist subscription licenses
from one organisation to another — or undoing a move you made earlier.
Menu-driven: list what's available, pick one, confirm, done.

## Requirements

- Python 3.8+
- A Mist API token for a user with access to the source org and every
  destination org you want to move licenses to

## Quick Start

**1. Install dependencies**

```bash
pip install -r requirements.txt
```

**2. Create your config file**

```bash
cp mist_license_transfer.ini.example mist_license_transfer.ini
```

**3. Edit `mist_license_transfer.ini`** with your API token and org IDs:

```ini
[mist]
base_url = https://api.eu.mist.com/api/v1
api_token = YOUR_API_TOKEN

[source]
org_id = YOUR_SOURCE_ORG_ID

[destinations]
org_ids = YOUR_DEST_ORG_ID_1, YOUR_DEST_ORG_ID_2
```

- `base_url` depends on which Mist cloud your orgs are in: `https://api.mist.com/api/v1` (US),
  `https://api.eu.mist.com/api/v1` (EU), `https://api.gc1.mist.com/api/v1` (GovCloud).
- `destinations.org_ids` can be one org, or several comma-separated — if you
  list more than one, the tool asks you which to use each time you run it.
- `mist_license_transfer.ini` is your own file with real credentials in it —
  it's gitignored and should never be committed or shared.

**4. Run it**

```bash
python3 mist_license_transfer.py
```

## Example Session

```
Juniper Mist License Transfer Tool
========================================
Fetching organization details...

Source Organization:
  Name: Acme HQ
  ID:   79017ec2-...

Destination Organization:
  Name: Acme Branch Offices
  ID:   fd15b80b-...

Fetching licenses from source organization: Acme HQ

#   Type       Subscription ID    Sub Type   Total   Avail   ...  Status      Dest Org
1   LICENSE    SUB-0101678        WAN        16      16      ...  Active      -
2   LICENSE    SUB-0094572        MAN        1329    1323    ...  Active      -
3   AMENDMENT  SUB-0094572        MAN        -6      -       ...  Moved Out   Acme Branch Offices

Select item to move/return (1-3) or 'q' to quit: 1
Enter quantity to move (max 16, or 'q' to cancel): 5

Confirm Transfer:
Subscription ID: SUB-0101678
Quantity:        5 (of 16 available, 16 total)
From:            Acme HQ
To:              Acme Branch Offices

Proceed with transfer? (y/N): y
Successfully moved 5 units of SUB-0101678

What would you like to do next?
  1) Move/return another license with Acme Branch Offices as the destination
  2) Choose a different destination organization
  3) Quit
Select an option (1-3):
```

Picking a **LICENSE** row moves capacity out of the source org to the chosen
destination. **Total** is the subscription's full size and **Avail** is what's
still in the source org after earlier moves out — you can only move up to
Avail, and subscriptions with nothing left aren't listed.

Picking an **AMENDMENT** row reverses a previous move, returning that
capacity to the source org. Every unexpired move out of the source org is
listed, whichever org it went to (shown in **Dest Org**), so you don't need
to switch destination to undo one. If there's nothing to move or return,
the tool says so and exits.

## Running Behind a TLS-Inspecting Proxy (e.g. Zscaler)

If your network intercepts and re-signs HTTPS traffic, plain TLS verification
against the Mist API will fail with a certificate error. Add a `[network]`
section to `mist_license_transfer.ini`:

```ini
[network]
ca_bundle = /path/to/zscaler-root-ca.pem
```

`ca_bundle` should point to a PEM file with your proxy's root CA certificate
(or a bundle that includes it) — your IT team or the proxy client app can
usually export this. Everything else in `[network]` is optional:

```ini
[network]
ca_bundle =
verify_ssl = true
http_proxy =
https_proxy =
```

- `verify_ssl = false` disables certificate verification entirely — only use
  this if you truly have no way to get the proxy's CA certificate.
- `http_proxy` / `https_proxy` are only needed if the standard `HTTP_PROXY`
  / `HTTPS_PROXY` / `NO_PROXY` environment variables aren't already set for
  your environment; `requests` picks those up automatically otherwise.

With no `[network]` section at all, nothing changes — the tool behaves
exactly as it does on a normal, non-intercepted connection.

## Options

| Flag | Purpose |
|---|---|
| `--dry-run` | Walk through the whole workflow, but skip the actual API call that makes a change — useful for a trial run |
| `--config <path>` | Use a specific config file, skipping the picker below |

```bash
python3 mist_license_transfer.py --dry-run
python3 mist_license_transfer.py --config other-orgs.ini
```

## Using Several Config Files

You can keep one `.ini` per set of orgs (e.g. `customer-a.ini`,
`customer-b.ini`), each copied from `mist_license_transfer.ini.example`. When
you run the tool without `--config`, it looks for `.ini` files in the
script's folder and in your current folder:

- **one file** — it's used automatically
- **several** — you're shown a numbered list (with each file's source org ID)
  and asked which to use

```
Available Config Files:
1   customer-a.ini                           source org 79017ec2-...
2   customer-b.ini                           source org fd15b80b-...

Select config file (1-2) or 'q' to quit:
```

All `*.ini` files are gitignored (only the `.example` template is tracked),
so tokens in any of them stay out of version control.

## A Couple of Notes

- Moving licenses changes real billing/subscription allocation between orgs —
  read the confirmation summary before answering `y`.
- The tool keeps running after each change so you can make several moves in
  one session; choose "Quit" from the menu when you're done.
