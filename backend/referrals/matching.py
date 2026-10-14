"""Candidate scoring and offer generation for incoming referrals."""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import eligibility
from .events import record_event
from .models import Agent, Offer, Referral, ReferralEvent

OFFER_TTL_MINUTES = 10
MAX_OFFERS_PER_REFERRAL = 3

# Weights tuned during the Q2 spike; see notebook in the pilot planning doc.
WEIGHT_GEO = 0.30
WEIGHT_PRICE = 0.20
WEIGHT_PERFORMANCE = 0.30
WEIGHT_CAPACITY = 0.20
ELIGIBILITY_PENALTY = 0.10


def score_agent(agent, referral):
    """Score an agent for a referral. Higher is better."""
    explanation = {}

    geo = 0.0
    if referral.state in (agent.licensed_states or []):
        geo += 0.5
    if any(referral.postal_code.startswith(p) for p in (agent.service_area_zip_prefixes or [])):
        geo += 0.5
    explanation["geo_fit"] = round(geo, 3)

    span = max(agent.maximum_price - agent.minimum_price, 1)
    midpoint = agent.minimum_price + span / 2
    price = max(0.0, 1.0 - abs(referral.estimated_price - midpoint) / max(midpoint, 1))
    explanation["price_fit"] = round(price, 3)

    performance = agent.performance_score if agent.performance_score is not None else 1.0
    explanation["performance"] = round(performance, 3)

    capacity_headroom = max(agent.capacity - agent.active_referral_count, 0)
    capacity = min(capacity_headroom / max(agent.capacity, 1), 1.0)
    explanation["capacity"] = round(capacity, 3)

    score = (
        WEIGHT_GEO * geo
        + WEIGHT_PRICE * price
        + WEIGHT_PERFORMANCE * performance
        + WEIGHT_CAPACITY * capacity
    )

    snapshot = eligibility.evaluate(agent, referral)
    penalty = ELIGIBILITY_PENALTY * len(snapshot["reasons"])
    score -= penalty
    explanation["eligibility_penalty"] = round(-penalty, 3)
    explanation["total"] = round(score, 3)

    return score, explanation, snapshot


def build_offers(referral):
    """Rank eligible agents for a referral and create offers for the top candidates."""
    with transaction.atomic():
        locked_referral = Referral.objects.select_for_update().get(pk=referral.pk)
        return _build_offers_locked(locked_referral)


def advance_referral(referral_id):
    """Expire overdue offers and safely start another matching batch if needed."""
    with transaction.atomic():
        referral = Referral.objects.select_for_update().get(pk=referral_id)
        if referral.status in (Referral.Status.ASSIGNED, Referral.Status.CLOSED):
            Offer.objects.filter(
                referral=referral, status=Offer.Status.PENDING
            ).update(status=Offer.Status.DECLINED, responded_at=timezone.now())
            return []

        now = timezone.now()
        expired = list(
            Offer.objects.select_for_update().filter(
                referral=referral,
                status=Offer.Status.PENDING,
                expires_at__lte=now,
            )
        )
        for offer in expired:
            offer.status = Offer.Status.EXPIRED
            offer.save(update_fields=["status"])
            record_event(
                referral,
                ReferralEvent.Type.OFFER_EXPIRED,
                actor="system",
                payload={"offer_id": offer.id},
            )

        if Offer.objects.filter(referral=referral, status=Offer.Status.PENDING).exists():
            return []
        return _build_offers_locked(referral)


def process_expired_offers():
    """Advance every referral with an expired offer; safe to run repeatedly."""
    referral_ids = list(
        Offer.objects.filter(
            status=Offer.Status.PENDING,
            expires_at__lte=timezone.now(),
        )
        .values_list("referral_id", flat=True)
        .distinct()
    )
    advanced = 0
    for referral_id in referral_ids:
        advance_referral(referral_id)
        advanced += 1
    return advanced


def _build_offers_locked(referral):
    if referral.status in (Referral.Status.ASSIGNED, Referral.Status.CLOSED):
        return []
    if Offer.objects.filter(referral=referral, status=Offer.Status.PENDING).exists():
        return []

    now = timezone.now()

    existing_agent_ids = set(
        Offer.objects.filter(referral=referral).values_list("agent_id", flat=True)
    )
    candidates = []
    agents = (
        Agent.objects.filter(active=True)
        .exclude(id__in=existing_agent_ids)
        .annotate(active_claim_count=Count("claims", filter=Q(claims__active=True)))
    )
    for agent in agents:
        agent.active_referral_count = agent.active_claim_count
        score, explanation, snapshot = score_agent(agent, referral)
        if not snapshot["eligible"]:
            continue
        candidates.append((score, agent, explanation, snapshot))

    candidates.sort(key=lambda item: item[0], reverse=True)

    record_event(
        referral,
        ReferralEvent.Type.CANDIDATES_GENERATED,
        actor="matching",
        payload={"considered": len(candidates)},
    )

    offers = []
    for score, agent, explanation, snapshot in candidates[:MAX_OFFERS_PER_REFERRAL]:
        offer = Offer.objects.create(
            referral=referral,
            agent=agent,
            score=score,
            score_explanation=explanation,
            eligibility_snapshot=snapshot,
            expires_at=now + timedelta(minutes=OFFER_TTL_MINUTES),
        )
        offers.append(offer)
        record_event(
            referral,
            ReferralEvent.Type.OFFER_SENT,
            actor="matching",
            payload={"agent_id": agent.id, "score": round(score, 3)},
        )

    referral.status = Referral.Status.OFFERED if offers else Referral.Status.NEW
    referral.save(update_fields=["status"])
    return offers
