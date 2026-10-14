"""Claim handling: an agent accepts an offer and takes ownership of the referral."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import eligibility, matching
from .events import record_event
from .models import Agent, Claim, NotificationOutbox, Offer, Referral, ReferralEvent


class ClaimError(Exception):
    def __init__(self, message, code="claim_failed"):
        super().__init__(message)
        self.code = code


def claim_offer(offer, agent):
    """Accept an offer and safely assign its referral."""
    try:
        with transaction.atomic():
            initial_offer = Offer.objects.only("referral_id").get(
                pk=offer.pk,
                agent_id=agent.pk,
            )
            referral = Referral.objects.select_for_update().get(pk=initial_offer.referral_id)
            locked_agent = Agent.objects.select_for_update().get(pk=agent.pk)
            offer = Offer.objects.select_for_update().get(
                pk=offer.pk,
                agent_id=locked_agent.pk,
                referral_id=referral.pk,
            )

            if referral.status == Referral.Status.ASSIGNED or Claim.objects.filter(
                referral=referral, active=True
            ).exists():
                raise ClaimError(
                    "This referral has already been claimed.",
                    code="already_claimed",
                )
            if offer.status != Offer.Status.PENDING:
                raise ClaimError("This offer is no longer open.", code="offer_not_open")
            if offer.expires_at <= timezone.now():
                raise ClaimError("This offer has expired.", code="offer_expired")
            if referral.status == Referral.Status.CLOSED:
                raise ClaimError("This referral is no longer claimable.", code="already_claimed")

            active_count = Claim.objects.filter(agent=locked_agent, active=True).count()
            locked_agent.active_referral_count = active_count
            eligibility_result = eligibility.evaluate(locked_agent, referral)
            if not eligibility_result["eligible"]:
                raise ClaimError(
                    "You are no longer eligible to claim this referral.",
                    code="agent_ineligible",
                )

            now = timezone.now()
            try:
                with transaction.atomic():
                    claim = Claim.objects.create(referral=referral, agent=locked_agent)
            except IntegrityError as exc:
                raise ClaimError(
                    "This referral has already been claimed.",
                    code="already_claimed",
                ) from exc

            referral.status = Referral.Status.ASSIGNED
            referral.assigned_agent = locked_agent
            referral.save(update_fields=["status", "assigned_agent"])

            Offer.objects.filter(
                referral=referral, status=Offer.Status.PENDING
            ).exclude(pk=offer.pk).update(
                status=Offer.Status.DECLINED,
                responded_at=now,
            )
            offer.status = Offer.Status.ACCEPTED
            offer.responded_at = now
            offer.save(update_fields=["status", "responded_at"])

            locked_agent.active_referral_count = active_count + 1
            locked_agent.save(update_fields=["active_referral_count"])
            NotificationOutbox.objects.create(
                claim=claim,
                next_attempt_at=now,
            )

            record_event(
                referral,
                ReferralEvent.Type.CLAIM_SUCCEEDED,
                actor=locked_agent.name,
                payload={"offer_id": offer.id, "claim_id": claim.id},
            )
            return claim
    except Offer.DoesNotExist as exc:
        raise ClaimError(
            "Offer not found or not assigned to this agent.",
            code="offer_not_found",
        ) from exc
    except Referral.DoesNotExist as exc:
        raise ClaimError(
            "The referral associated with this offer was not found.",
            code="referral_not_found",
        ) from exc
    except Agent.DoesNotExist as exc:
        raise ClaimError("Agent profile not found.", code="agent_not_found") from exc


def decline_offer(offer, agent):
    """Record that the agent passed on the offer under the referral lock."""
    try:
        with transaction.atomic():
            referral = Referral.objects.select_for_update().get(pk=offer.referral_id)
            locked_offer = Offer.objects.select_for_update().get(
                pk=offer.pk,
                agent_id=agent.pk,
                referral_id=referral.pk,
            )
            if locked_offer.status != Offer.Status.PENDING:
                raise ClaimError("This offer is no longer open.", code="offer_not_open")
            if locked_offer.expires_at <= timezone.now():
                raise ClaimError("This offer has expired.", code="offer_expired")
            if referral.status in (Referral.Status.ASSIGNED, Referral.Status.CLOSED):
                raise ClaimError("This referral is no longer open.", code="offer_not_open")

            locked_offer.status = Offer.Status.DECLINED
            locked_offer.responded_at = timezone.now()
            locked_offer.save(update_fields=["status", "responded_at"])
            record_event(
                referral,
                ReferralEvent.Type.OFFER_DECLINED,
                actor=agent.name,
                payload={"offer_id": locked_offer.id},
            )
            matching.advance_referral(referral.pk)
            return locked_offer
    except Referral.DoesNotExist as exc:
        raise ClaimError("Referral not found.", code="referral_not_found") from exc
    except Offer.DoesNotExist as exc:
        raise ClaimError("Offer not found.", code="offer_not_found") from exc
