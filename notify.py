#!/usr/bin/env python3
"""
HuntHarvest v2 — SMS notifications via Twilio's REST API directly.
Not copied from AGSTOX's send_sms() - that's been fully replaced by APNs push there
(kept only as a compatibility wrapper), and HuntHarvest has no app to push to. Same
Twilio account/credentials, a fresh direct integration.
"""
import os
import logging
import requests

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")
TWILIO_TO_NUMBER = os.environ.get("TWILIO_TO_NUMBER")

log = logging.getLogger("notify")


def send_sms(message):
    """Best-effort - a notification failure should never break the caller's real work
    (a checkpoint locking, an event settling). Logs and returns False on any problem
    rather than raising."""
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
