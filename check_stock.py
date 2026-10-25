#!/usr/bin/env python3
"""
iPhone Duo stock checker - Apple Store Singapore
Checks whether iPhone Duo 512GB Star White is available for in-store pickup
at any Apple Store in Singapore, and watches the online delivery date.
Sends push notifications to your iPhone via the ntfy app.

Uses only the Python standard library (no installs needed).

Settings come from environment variables:
  NTFY_TOPIC   (required) your secret ntfy topic name
  PART_NUMBER  (optional) Apple part number to watch
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

# Singapore part number for iPhone Duo 512GB Star White (from apple.com/sg).
DEFAULT_PART = "MK2F4X/A"

PRODUCT_NAME = "iPhone Duo 512GB Star White"
BUY_URL = "https://www.apple.com/sg/shop/buy-iphone/iphone-duo/7.6-inch-display-512gb-star-white"
API_URL = "https://www.apple.com/sg/shop/sba/availability-message"
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
    """Ask Apple Singapore about pickup stock and delivery for this part."""
    params = urllib.parse.urlencode({"parts.0": part})
    req = urllib.request.Request(f"{API_URL}?{params}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse(data, part):
    """
    Pull out what we need from Apple's answer.
    Returns None if Apple didn't recognise the part, otherwise a dict:
      available (bool), stores (int), delivery (str), delivery_code (str)
    """
    content = (data.get("body") or {}).get("content") or []
    item = next((c for c in content if c.get("partNumber") == part), None)
    if item is None:
        return None
    delivery_msg = item.get("deliveryMessage") or {}
    options = delivery_msg.get("deliveryOptions") or []
    delivery = options[0].get("date", "") if options else ""
    return {
        "available": bool(item.get("availableAtAnyStore")),
        "stores": int(item.get("partAvailableStoresCount") or 0),
        "delivery": delivery,
        "delivery_code": item.get("encodedUpperDateString") or "",
        "buyable": bool(delivery_msg.get("isBuyable", True)),
    }


def check(part):
    last_error = None
    for attempt in range(2):
        try:
            result = parse(fetch_availability(part), part)
            if result is None:
                raise LookupError(f"Apple Singapore did not recognise part {part}")
            return result
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
            last_error = e
            print(f"Attempt {attempt + 1} failed: {e}")
            time.sleep(5)
    raise RuntimeError(f"Could not reach Apple: {last_error}")


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

    part = os.environ.get("PART_NUMBER", "").strip() or DEFAULT_PART
    state_path = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_path)
    errors = state.get("consecutive_errors", 0)

    try:
        r = check(part)
    except LookupError as e:
        print(e)
        if not state.get("part_warning_sent"):
            notify(topic, "Stock checker needs a fix",
                   f"Apple didn't recognise part {part}. Ask Claude to help update it.",
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

    print(f"{PRODUCT_NAME} ({part})")
    print(f"  Pickup in Singapore: {'AVAILABLE at ' + str(r['stores']) + ' store(s)' if r['available'] else 'not available at any store'}")
    print(f"  Online delivery date: {r['delivery'] or 'unknown'}")

    was_available = state.get("available", False)
    if r["available"] and not was_available:
        notify(topic, f"IN STOCK: {PRODUCT_NAME}",
               f"Available for pickup at {r['stores']} Apple Store(s) in Singapore. "
               "Tap to open the Apple Store and choose Pick up.",
               priority="urgent", tags="rotating_light,iphone")
    elif was_available and not r["available"]:
        notify(topic, "Sold out again",
               f"{PRODUCT_NAME} is no longer available for pickup in Singapore.",
               priority="low", tags="x")

    # Tell you if online delivery gets meaningfully earlier.
    old_code = state.get("delivery_code", "")
    if old_code and r["delivery_code"] and r["delivery_code"] < old_code:
        notify(topic, "Delivery date moved earlier",
               f"Online orders now deliver {r['delivery']} (was {state.get('delivery', old_code)}).",
               tags="truck")

    save_state(state_path, {
        "available": r["available"],
        "delivery": r["delivery"],
        "delivery_code": r["delivery_code"],
        "consecutive_errors": 0,
        "checked_at": int(time.time()),
    })


if __name__ == "__main__":
    main()
