"""Outbound notifications. The pilot uses a simulated SMS/email gateway."""

import logging
import re

logger = logging.getLogger("referrals.notifications")

PHONE_PATTERN = re.compile(r"^[0-9()+\-\s.]+$")


class NotificationError(Exception):
    pass


def send_claim_confirmation(claim, idempotency_key=None):
    """Notify the customer and agent that the referral was claimed.

    The pilot gateway is a stub, but it validates recipients the same way the
    real provider does.
    """
    referral = claim.referral
    if not PHONE_PATTERN.match(referral.customer_phone or ""):
        raise NotificationError("recipient_rejected")
    logger.info(
        "notification sent: referral=%s agent_id=%s idempotency_key=%s",
        referral.external_id,
        claim.agent_id,
        idempotency_key,
    )
