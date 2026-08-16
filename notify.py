#!/usr/bin/env python3
"""
HuntHarvest v2 — checkpoint-lock notifications.

Two mechanisms, only one actually usable right now:
  - send_sms(): direct Twilio REST API. Correctly implemented but currently BLOCKED -
    real test send confirmed carrier-rejected (error 30034, unregistered number for
    A2P messaging - needs real Twilio Brand/Campaign registration with Ashok's actual
    business info, not something to push through via API). Kept for whenever/if that
    registration completes; NOT called by the live pipeline today.
  - send_push_via_agstox(): what's ACTUALLY wired in. Relays through AGSTOX's own
    already-working push dispatcher (APNs to Ashok's iOS app + Web Push) via a small
    internal endpoint added to agstox_exchange.py for this purpose. Real test send
    confirmed delivered 2026-08-16 before this got wired into the live pipeline.
"""
import os
import logging
import requests

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = os.environ.get("TWILIO_TO_NUMBER")

AGSTOX_PUSH_URL = "https://exchange.agstox.com/api/internal/push"
AGSTOX_INTERNAL_TOKEN = os.environ.get("AGSTOX_INTERNAL_TOKEN")

log = logging.getLogger("notify")


def send_sms(message):
    """Currently blocked by carrier compliance (see module docstring) - kept correct
    and ready, not called by the live pipeline. Best-effort: never raises."""
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER]):
        log.warning("Twilio credentials not fully set - skipping SMS")
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        r = requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": TWILIO_FROM_NUMBER, "To": TWILIO_TO_NUMBER, "Body": message},
            timeout=10,
        )
        if r.status_code >= 300:
            log.error(f"Twilio send failed: {r.status_code} {r.text}")
            return False
        return True
    except Exception as e:
        log.error(f"Twilio send FAILED: {e}")
        return False


def send_push_via_agstox(title, body):
    """The real, working notification path - relays through AGSTOX's already-proven
    push dispatcher instead of standing up new infrastructure. Best-effort: a
    notification failure should never break the caller's real work (a checkpoint
    locking, an event settling)."""
    if not AGSTOX_INTERNAL_TOKEN:
        log.warning("AGSTOX_INTERNAL_TOKEN not set - skipping push")
        return False
    try:
        r = requests.post(
            AGSTOX_PUSH_URL,
            json={"token": AGSTOX_INTERNAL_TOKEN, "title": title, "body": body, "source": "huntharvest"},
            timeout=10,
        )
        if r.status_code >= 300:
            log.error(f"AGSTOX push relay failed: {r.status_code} {r.text}")
            return False
        ok = r.json().get("sent", False)
        if not ok:
            log.warning(f"AGSTOX push relay returned sent=false: {r.text}")
        return ok
    except Exception as e:
        log.error(f"AGSTOX push relay FAILED: {e}")
        return False
