#!/usr/bin/env python3
"""
Juniper Mist License Transfer Tool

Moves (amends) subscription capacity from one Mist organisation to another,
or reverses a previous move by "unamending" it. A single API token (from a
user with access to every org involved) is used throughout. Configuration -
API token, source org, candidate destination orgs, cloud base URL - lives in
an .ini file that is never committed to version control (see
mist_license_transfer.ini.example).
"""

import argparse
import configparser
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

DEFAULT_CONFIG_PATH = Path(__file__).with_name("mist_license_transfer.ini")
REQUEST_TIMEOUT = 20


# --------------------------------------------------------------------------
# Terminal colour helpers (no-op automatically when not a tty / NO_COLOR set)
# --------------------------------------------------------------------------

class Ansi:
    enabled = sys.stdout.isatty()

    RESET = "\033[0m" if enabled else ""
    BOLD = "\033[1m" if enabled else ""
    RED = "\033[31m" if enabled else ""
    GREEN = "\033[32m" if enabled else ""
    YELLOW = "\033[33m" if enabled else ""
    CYAN = "\033[36m" if enabled else ""
    DIM = "\033[2m" if enabled else ""


def info(msg):
    print(f"{Ansi.CYAN}{msg}{Ansi.RESET}")


def success(msg):
    print(f"{Ansi.GREEN}{msg}{Ansi.RESET}")


def warn(msg):
    print(f"{Ansi.YELLOW}{msg}{Ansi.RESET}")


def error(msg):
    print(f"{Ansi.RED}{msg}{Ansi.RESET}", file=sys.stderr)


class TransferError(Exception):
    """Raised for any recoverable, user-facing failure."""


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        example = path.with_suffix(".ini.example")
        raise TransferError(
            f"Config file not found: {path}\n"
            f"Copy {example.name} to {path.name} and fill in your org IDs and API token."
        )

    parser = configparser.ConfigParser()
    parser.read(path)

    required = {
        "mist": ["base_url", "api_token"],
        "source": ["org_id"],
        "destinations": ["org_ids"],
    }

    missing = []
    for section, keys in required.items():
        if section not in parser:
            missing.append(f"[{section}] section")
            continue
        for key in keys:
            if not parser[section].get(key, "").strip():
                missing.append(f"[{section}] {key}")

    if missing:
        raise TransferError(
            "Config is missing required values:\n  - " + "\n  - ".join(missing)
        )

    dest_org_ids = [
        org_id.strip()
        for org_id in parser["destinations"]["org_ids"].replace("\n", ",").split(",")
        if org_id.strip()
    ]
    if not dest_org_ids:
        raise TransferError("[destinations] org_ids must list at least one destination org ID.")

    verify, proxies = _parse_network_settings(parser)

    return {
        "base_url": parser["mist"]["base_url"].rstrip("/"),
        "api_token": parser["mist"]["api_token"].strip(),
        "source_org_id": parser["source"]["org_id"].strip(),
        "dest_org_ids": dest_org_ids,
        "verify": verify,
        "proxies": proxies,
    }


def _parse_network_settings(parser: configparser.ConfigParser):
    """Reads the optional [network] section used to work behind a TLS-inspecting
    proxy (e.g. Zscaler). Every setting here is optional - with no [network]
    section at all, behaviour is unchanged (system CA store, env-var proxies)."""
    if "network" not in parser:
        return True, {}

    section = parser["network"]

    ca_bundle = section.get("ca_bundle", "").strip()
    verify_ssl = section.getboolean("verify_ssl", fallback=True)

    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser()
        if not ca_path.is_file():
            raise TransferError(
                f"[network] ca_bundle does not exist: {ca_path}\n"
                f"This should be a PEM file containing your proxy's root CA "
                f"certificate (e.g. exported from Zscaler)."
            )
        verify = str(ca_path)
    elif not verify_ssl:
        verify = False
    else:
        verify = True

    proxies = {}
    http_proxy = section.get("http_proxy", "").strip()
    https_proxy = section.get("https_proxy", "").strip()
    if http_proxy:
        proxies["http"] = http_proxy
    if https_proxy:
        proxies["https"] = https_proxy

    return verify, proxies


# --------------------------------------------------------------------------
# API helpers
# --------------------------------------------------------------------------

def build_session(cfg: dict) -> requests.Session:
    """Builds the shared HTTP session, wired up for a TLS-inspecting proxy
    (e.g. Zscaler) when [network] settings are present in the config."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {cfg['api_token']}",
        "Content-Type": "application/json",
    })
    session.verify = cfg["verify"]
    if cfg["proxies"]:
        session.proxies.update(cfg["proxies"])

    if cfg["verify"] is False:
        warn(
            "TLS certificate verification is DISABLED ([network] verify_ssl = false). "
            "Traffic to the Mist API will not be authenticated - only use this as a last resort."
        )
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

    return session


def api_request(session: requests.Session, method: str, url: str, **kwargs):
    """Wraps session.request with consistent error handling. Returns parsed JSON."""
    try:
        response = session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
    except requests.exceptions.SSLError as e:
        raise TransferError(
            f"TLS certificate verification failed for {url}: {e}\n"
            f"If you're behind a TLS-inspecting proxy (e.g. Zscaler), set [network] ca_bundle "
            f"in your config to the path of its root CA certificate (PEM format)."
        )
    except requests.exceptions.ProxyError as e:
        raise TransferError(f"Could not reach the proxy for {url}: {e}\nCheck [network] http_proxy/https_proxy in your config.")
    except requests.exceptions.Timeout:
        raise TransferError(f"Request to {url} timed out after {REQUEST_TIMEOUT}s.")
    except requests.exceptions.ConnectionError:
        raise TransferError(f"Could not connect to {url}. Check your network and the base_url in your config.")
    except requests.exceptions.HTTPError as e:
        detail = _extract_error_detail(e.response)
        raise TransferError(f"HTTP {e.response.status_code} from {url}{detail}")
    except json.JSONDecodeError:
        raise TransferError(f"Unexpected (non-JSON) response from {url}.")
    except requests.exceptions.RequestException as e:
        raise TransferError(f"Request to {url} failed: {e}")


def _extract_error_detail(response) -> str:
    if response is None:
        return ""
    try:
        payload = response.json()
        return f"\n{json.dumps(payload, indent=2)}"
    except (json.JSONDecodeError, ValueError):
        text = response.text.strip()
        return f"\n{text}" if text else ""


def get_org_name(session: requests.Session, base_url: str, org_id: str) -> str:
    try:
        data = api_request(session, "GET", f"{base_url}/orgs/{org_id}")
        return data.get("name", "Unknown")
    except TransferError as e:
        warn(f"Could not fetch organisation name for {org_id}: {e}")
        return "Unknown"


def select_destination_org(session: requests.Session, base_url: str, dest_org_ids: list) -> tuple:
    """Resolves the destination org to use. Prompts the user when more than one is configured."""
    if len(dest_org_ids) == 1:
        org_id = dest_org_ids[0]
        return org_id, get_org_name(session, base_url, org_id)

    info("\nMultiple destination orgs configured - fetching org names...")
    orgs = [(org_id, get_org_name(session, base_url, org_id)) for org_id in dest_org_ids]

    print(f"\n{Ansi.BOLD}Available Destination Organizations:{Ansi.RESET}")
    for i, (org_id, name) in enumerate(orgs, 1):
        print(f"{i:<3} {name:<36} {org_id}")

    idx = prompt_index(len(orgs), f"\nSelect destination org (1-{len(orgs)}) or 'q' to quit: ")
    return orgs[idx]


# --------------------------------------------------------------------------
# Data shaping
# --------------------------------------------------------------------------

def collect_valid_licenses(licenses_data: dict) -> list:
    now = datetime.now().timestamp()
    result = []
    for lic in licenses_data.get("licenses", []):
        start = lic.get("start_time", 0)
        end = lic.get("end_time", 0)
        if start <= now <= end and lic.get("subscription_id"):
            result.append({
                "subscription_id": lic.get("subscription_id"),
                "type": lic.get("type", "unknown"),
                "quantity": lic.get("quantity", 0),
                "start_time": start,
                "end_time": end,
            })
    return result


def collect_returnable_amendments(licenses_data: dict, dest_org_id: str) -> list:
    result = []
    for amend in licenses_data.get("amendments", []):
        if amend.get("quantity", 0) < 0 and amend.get("dst_org_id") == dest_org_id:
            result.append({
                "amendment_id": amend.get("id"),
                "subscription_id": amend.get("subscription_id"),
                "type": amend.get("type", "unknown"),
                "quantity": amend.get("quantity", 0),
                "dst_org_id": amend.get("dst_org_id"),
                "start_time": amend.get("start_time", 0),
                "end_time": amend.get("end_time", 0),
            })
    return result


def fmt_date(ts) -> str:
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return "N/A"


# --------------------------------------------------------------------------
# Display / selection
# --------------------------------------------------------------------------

def display_items(source_org_name: str, dest_org_name: str, licenses: list, amendments: list) -> list:
    width = 132
    print("\n" + "=" * width)
    print(f"VALID SUBSCRIPTIONS AND AMENDMENTS IN SOURCE ORGANIZATION: {source_org_name}")
    print("=" * width)
    header = (
        f"{'#':<3} {'Type':<10} {'Subscription ID':<18} {'Sub Type':<12} "
        f"{'Quantity':<8} {'Start Date':<12} {'End Date':<12} {'Status':<12} {'Dest Org':<36}"
    )
    print(header)
    print("-" * width)

    items = []
    idx = 1
    for lic in licenses:
        print(
            f"{idx:<3} {'LICENSE':<10} {lic['subscription_id']:<18} {lic['type']:<12} "
            f"{lic['quantity']:<8} {fmt_date(lic['start_time']):<12} {fmt_date(lic['end_time']):<12} "
            f"{'Active':<12} {'-':<36}"
        )
        items.append(("license", lic))
        idx += 1

    for amend in amendments:
        print(
            f"{idx:<3} {'AMENDMENT':<10} {amend['subscription_id']:<18} {amend['type']:<12} "
            f"{amend['quantity']:<8} {fmt_date(amend['start_time']):<12} {fmt_date(amend['end_time']):<12} "
            f"{'Moved Out':<12} {amend['dst_org_id']:<36}"
        )
        items.append(("amendment", amend))
        idx += 1

    print("-" * width)
    print(
        f"{Ansi.DIM}Legend: LICENSE = Available to move out | "
        f"AMENDMENT = Previously moved out to {dest_org_name} (can be returned){Ansi.RESET}"
    )
    return items


def prompt_index(count: int, prompt: str) -> int:
    """Prompts for a 1-based selection and returns the corresponding 0-based index. 'q' exits."""
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            raise TransferError("No input available (stdin closed).")
        if raw.lower() == "q":
            info("Exiting...")
            sys.exit(0)
        if not raw.isdigit():
            warn("Please enter a valid number or 'q' to quit.")
            continue
        idx = int(raw) - 1
        if 0 <= idx < count:
            return idx
        warn(f"Please enter a number between 1 and {count}.")


def prompt_selection(items: list):
    idx = prompt_index(len(items), f"\nSelect item to move/return (1-{len(items)}) or 'q' to quit: ")
    return items[idx]


def prompt_menu(title: str, options: list):
    """options: list of (label, value). Prints a numbered menu and returns the chosen value."""
    print(f"\n{Ansi.BOLD}{title}{Ansi.RESET}")
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}) {label}")
    idx = prompt_index(len(options), f"Select an option (1-{len(options)}): ")
    return options[idx][1]


def prompt_quantity(max_quantity: int) -> int:
    while True:
        try:
            raw = input(f"Enter quantity to move (max {max_quantity}, or 'q' to cancel): ").strip()
        except EOFError:
            raise TransferError("No input available (stdin closed).")
        if raw.lower() == "q":
            info("Cancelled.")
            sys.exit(0)
        if not raw.isdigit():
            warn("Please enter a valid number.")
            continue
        qty = int(raw)
        if 1 <= qty <= max_quantity:
            return qty
        warn(f"Please enter a quantity between 1 and {max_quantity}.")


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        return False


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def move_license(cfg: dict, session: requests.Session, source_org_name: str, dest_org_id: str, dest_org_name: str, license_item: dict, dry_run: bool):
    max_quantity = license_item["quantity"]
    quantity = prompt_quantity(max_quantity)

    print(f"\n{Ansi.BOLD}Confirm Transfer:{Ansi.RESET}")
    print(f"Subscription ID: {license_item['subscription_id']}")
    print(f"Type:            {license_item['type']}")
    print(f"Quantity:        {quantity}")
    print(f"From:            {source_org_name} ({cfg['source_org_id']})")
    print(f"To:              {dest_org_name} ({dest_org_id})")

    if not confirm("\nProceed with transfer? (y/N): "):
        info("Transfer cancelled.")
        return

    if dry_run:
        warn("[dry-run] Skipping API call - no changes made.")
        return

    info("\nInitiating license move...")
    payload = {
        "op": "amend",
        "subscription_id": license_item["subscription_id"],
        "dst_org_id": dest_org_id,
        "quantity": quantity,
    }
    api_request(
        session, "PUT", f"{cfg['base_url']}/orgs/{cfg['source_org_id']}/licenses",
        json=payload,
    )

    success(f"Successfully moved {quantity} units of {license_item['subscription_id']}")
    print(f"From: {source_org_name} ({cfg['source_org_id']})")
    print(f"To:   {dest_org_name} ({dest_org_id})")

    remaining = max_quantity - quantity
    if remaining > 0:
        info(f"Remaining in {source_org_name}: {remaining} units")
    else:
        info(f"All units of this subscription have been moved from {source_org_name}")

    success("\nMove operation completed successfully!")
    print(f"Note: please verify the subscription appears in {dest_org_name}.")


def return_amendment(cfg: dict, session: requests.Session, source_org_name: str, amendment_item: dict, dry_run: bool):
    print(f"\nYou selected an amendment (move record) for subscription {amendment_item['subscription_id']}.")
    print(f"This will return {abs(amendment_item['quantity'])} licenses from the destination org back to {source_org_name}.")
    print(f"Amendment ID:     {amendment_item['amendment_id']}")
    print(f"Destination Org:  {amendment_item['dst_org_id']}")

    if not confirm("\nProceed with return (unamend)? (y/N): "):
        info("Return cancelled.")
        return

    if dry_run:
        warn("[dry-run] Skipping API call - no changes made.")
        return

    info("\nInitiating license return...")
    payload = {"op": "unamend", "amendment_id": amendment_item["amendment_id"]}
    api_request(
        session, "PUT", f"{cfg['base_url']}/orgs/{cfg['source_org_id']}/licenses",
        json=payload,
    )

    success(f"Successfully returned {abs(amendment_item['quantity'])} units of {amendment_item['subscription_id']}")
    print(f"From:    Destination org ({amendment_item['dst_org_id']})")
    print(f"Back to: {source_org_name} ({cfg['source_org_id']})")
    success("\nReturn (unamend) operation completed successfully!")
    print(f"Note: please verify the subscription is back in {source_org_name}.")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Move or return Mist subscription licenses between two orgs.")
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"Path to config .ini file (default: {DEFAULT_CONFIG_PATH.name})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without calling the Mist API to make changes.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print(f"{Ansi.BOLD}Juniper Mist License Transfer Tool{Ansi.RESET}")
    print("=" * 40)
    if args.dry_run:
        warn("Running in --dry-run mode: no changes will be made.\n")

    cfg = load_config(args.config)
    multi_dest = len(cfg["dest_org_ids"]) > 1
    session = build_session(cfg)

    info("Fetching organization details...")
    source_org_name = get_org_name(session, cfg["base_url"], cfg["source_org_id"])
    print(f"\nSource Organization:")
    print(f"  Name: {source_org_name}")
    print(f"  ID:   {cfg['source_org_id']}")

    dest_org_id, dest_org_name = select_destination_org(session, cfg["base_url"], cfg["dest_org_ids"])

    while True:
        print(f"\nDestination Organization:")
        print(f"  Name: {dest_org_name}")
        print(f"  ID:   {dest_org_id}")

        print(f"\nFetching licenses from source organization: {source_org_name}")
        licenses_data = api_request(
            session, "GET", f"{cfg['base_url']}/orgs/{cfg['source_org_id']}/licenses"
        )

        if not licenses_data:
            warn("No license data found.")
        else:
            valid_licenses = collect_valid_licenses(licenses_data)
            valid_amendments = collect_returnable_amendments(licenses_data, dest_org_id)

            if not valid_licenses and not valid_amendments:
                warn(f"No valid subscriptions or amendments available for {dest_org_name}.")
            else:
                items = display_items(source_org_name, dest_org_name, valid_licenses, valid_amendments)
                item_type, item = prompt_selection(items)

                if item_type == "license":
                    move_license(cfg, session, source_org_name, dest_org_id, dest_org_name, item, args.dry_run)
                else:
                    return_amendment(cfg, session, source_org_name, item, args.dry_run)

        if multi_dest:
            next_action = prompt_menu("What would you like to do next?", [
                (f"Move/return another license with {dest_org_name} as the destination", "same"),
                ("Choose a different destination organization", "change"),
                ("Quit", "quit"),
            ])
        else:
            next_action = prompt_menu("What would you like to do next?", [
                ("Make another change", "same"),
                ("Quit", "quit"),
            ])

        if next_action == "quit":
            break
        if next_action == "change":
            dest_org_id, dest_org_name = select_destination_org(session, cfg["base_url"], cfg["dest_org_ids"])

    print("\nScript completed.")


if __name__ == "__main__":
    try:
        main()
    except TransferError as e:
        error(f"\nError: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print()
        warn("Cancelled by user.")
        sys.exit(130)
