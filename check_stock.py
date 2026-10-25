#!/usr/bin/env python3
"""
iPhone Duo stock checker - Apple Store Singapore
Checks in-store pickup availability for iPhone Duo 512GB Star White
and sends a push notification to your iPhone via the ntfy app.

Uses only the Python standard library (no installs needed).

Settings come from environment variables:
  NTFY_TOPIC   (required) your secret ntfy topic name
  PART_NUMBERS (optional) comma-separated Apple part numbers to try
  STATE_FILE   (optional) where to remember the last result
  TEST_NOTIFY  (optional) set to "1" to send a test notification and exit
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --- Settings -------------------------------------------------------------

# Apple part number for iPhone Duo 512GB Star White (Singapore model).
# Singapore models end in "ZP/A". The first number Apple recognises is used.
DEFAULT_PARTS = "MK2F4ZP/A,MK2F4ZA/A"

PRODUCT_NAME = "iPhone Duo 512GB Star White"
BUY_URL = "https://www.apple.com/sg/shop/buy-iphone/iphone-duo"
API_URL = "https://www.apple.com/sg/shop/fulfillment-messages"
NTFY_SERVER = "https://ntfy.sh"

# Only warn about errors after this many failed checks in a row,
# so one hiccup doesn't buzz your phone.
ERROR_ALERT_AFTER = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-SG,en;q=0.9",
    "Referer": BUY_URL,
}


# --- Notifications --------------------------------------------------------

def notify(topic, title, message, priority="default", tags="", click=BUY_URL):
    """Send a push notification through ntfy."""
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{urllib.parse.quote(topic)}",
        data=message.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Click": click,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"Notification sent ({resp.status}): {title}")
    except Exception as e:  # noqa: BLE001
        print(f"Could not send notification: {e}", file=sys.stderr)


# --- Apple availability ---------------------------------------------------

def fetch_availability(part):
    """Ask Apple which Singapore stores have this part for pickup."""
    params = urllib.parse.urlencode({
        "pl": "true",
        "mts.0": "regular",
        "parts.0": part,
        "location": "Singapore",
    })
    req = urllib.request.Request(f"{API_URL}?{params}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_stores(data, part):
    """
    Turn Apple's response into a list of stores.
    Returns (recognised, stores) where stores is a list of
    {"name", "available", "quote"}.
    """
    stores_raw = (
        data.get("body", {})
        .get("content", {})
        .get("pickupMessage", {})
        .get("stores", [])
    )
    stores = []
    recognised = False
    for s in stores_raw:
        info = (s.get("partsAvailability") or {}).get(part)
        if not info:
            continue
        recognised = True
        display = (info.get("pickupDisplay") or "").lower()
        quote = info.get("pickupSearchQuote") or info.get("storePickupQuote") or ""
        stores.append({
            "name": s.get("storeName") or s.get("storeNumber") or "Apple Store",
            "available": display == "available",
            "quote": quote,
        })
    return recognised, stores


def check():
    """Try each candidate part number. Returns (part, stores)."""
    parts = [p.strip() for p in os.environ.get("PART_NUMBERS", DEFAULT_PARTS).split(",") if p.strip()]
    last_error = None
    for part in parts:
        for attempt in range(2):
            try:
                data = fetch_availability(part)
                recognised, stores = parse_stores(data, part)
                if recognised:
                    return part, stores
                print(f"Apple did not recognise part {part}")
                break
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
                last_error = e
                print(f"Attempt {attempt + 1} for {part} failed: {e}")
                time.sleep(5)
    if last_error:
        raise RuntimeError(f"Could not reach Apple: {last_error}")
    raise LookupError("None of the part numbers were recognised by Apple Singapore")


# --- State (so you only get pinged when something changes) ----------------

def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    with open(path, "w") as f:
        json.dump(state, f)


# --- Main -----------------------------------------------------------------

def main():
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        sys.exit("NTFY_TOPIC is not set. Add it as a GitHub secret.")

    if os.environ.get("TEST_NOTIFY") == "1":
        notify(topic, "Test: stock checker is set up",
               f"You'll get an alert here when {PRODUCT_NAME} is available for pickup in Singapore.",
               tags="white_check_mark")
        return

    state_path = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_path)
    prev_available = set(state.get("available_stores", []))
    errors = state.get("consecutive_errors", 0)

    try:
        part, stores = check()
    except LookupError as e:
        print(e)
        if not state.get("part_warning_sent"):
            notify(topic, "Stock checker needs a fix",
                   "Apple didn't recognise the part number for the 512GB Star White. "
                   "Ask Claude to help update PART_NUMBERS.",
                   priority="high", tags="warning")
            state["part_warning_sent"] = True
        save_state(state_path, state)
        return
    except RuntimeError as e:
        errors += 1
        print(e)
        if errors == ERROR_ALERT_AFTER:
            notify(topic, "Stock checker can't reach Apple",
                   f"The last {errors} checks failed. It will keep trying.",
                   tags="warning")
        state["consecutive_errors"] = errors
        save_state(state_path, state)
        return

    if errors >= ERROR_ALERT_AFTER:
        notify(topic, "Stock checker is working again", "Back to checking Apple.", tags="ok")

    available = {s["name"] for s in stores if s["available"]}
    print(f"Part {part}:")
    for s in stores:
        print(f"  {s['name']}: {'AVAILABLE' if s['available'] else 'not available'} {s['quote']}")

    newly = available - prev_available
    gone = prev_available - available

    if newly:
        lines = [f"- {s['name']}: {s['quote'] or 'available'}" for s in stores if s["name"] in newly]
        notify(topic, f"IN STOCK: {PRODUCT_NAME}",
               "Available for pickup now:\n" + "\n".join(lines) + "\nTap to open the Apple Store.",
               priority="urgent", tags="rotating_light,iphone")
    if gone and not available:
        notify(topic, "Sold out again",
               f"{PRODUCT_NAME} is no longer available at: {', '.join(sorted(gone))}.",
               priority="low", tags="x")

    save_state(state_path, {
        "available_stores": sorted(available),
        "consecutive_errors": 0,
        "part": part,
        "checked_at": int(time.time()),
    })


if __name__ == "__main__":
    main()
