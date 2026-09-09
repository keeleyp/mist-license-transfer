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

#   Type       Subscription ID    Sub Type   Quantity ...
1   LICENSE    SUB-0101678        WAN        16       ...
2   LICENSE    SUB-0094572        MAN        1329     ...
3   AMENDMENT  SUB-0363543        MAN        -6       ...   (previously moved out)

Select item to move/return (1-3) or 'q' to quit: 1
Enter quantity to move (max 16, or 'q' to cancel): 5

Confirm Transfer:
Subscription ID: SUB-0101678
Quantity:        5
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

Picking a **LICENSE** row moves capacity out of the source org.
Picking an **AMENDMENT** row reverses a previous move, returning that
capacity to the source org.

## Options

| Flag | Purpose |
|---|---|
| `--dry-run` | Walk through the whole workflow, but skip the actual API call that makes a change — useful for a trial run |
| `--config <path>` | Use a config file other than `mist_license_transfer.ini` (e.g. for a different pair of orgs) |

```bash
python3 mist_license_transfer.py --dry-run
python3 mist_license_transfer.py --config other-orgs.ini
```

## A Couple of Notes

- Moving licenses changes real billing/subscription allocation between orgs —
  read the confirmation summary before answering `y`.
- The tool keeps running after each change so you can make several moves in
  one session; choose "Quit" from the menu when you're done.
