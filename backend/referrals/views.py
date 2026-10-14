from datetime import timedelta

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from . import claims, eligibility, matching
from .events import record_event
from .models import Agent, Claim, Offer, Referral, ReferralEvent
from .serializers import (
    OfferSerializer,
    OpsReferralSerializer,
    ReferralEventSerializer,
    ReferralSerializer,
)


class IsOpsUser(BasePermission):
    message = "Operations access required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_staff
        )


class OpsReferralPagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200


class OpsOverrideSerializer(serializers.Serializer):
    agent_id = serializers.IntegerField(min_value=1, allow_null=True)
    note = serializers.CharField(max_length=500, allow_blank=False)


def _agent_for(request):
    return getattr(request.user, "agent_profile", None)


@api_view(["GET"])
def my_offers(request):
    """Offers for the signed-in agent's queue."""
    agent = _agent_for(request)
    if agent is None:
        return Response({"detail": "No agent profile."}, status=status.HTTP_403_FORBIDDEN)

    if not agent.active or agent.suspended:
        return Response([], status=status.HTTP_200_OK)
    active_count = Claim.objects.filter(agent=agent, active=True).count()
    if agent.active_referral_count != active_count:
        Agent.objects.filter(pk=agent.pk).update(active_referral_count=active_count)
        agent.active_referral_count = active_count

    expired_referral_ids = list(
        Offer.objects.filter(
            agent=agent,
            status=Offer.Status.PENDING,
            expires_at__lte=timezone.now(),
        )
        .values_list("referral_id", flat=True)
        .distinct()
    )
    for referral_id in expired_referral_ids:
        matching.advance_referral(referral_id)

    offers = (
        Offer.objects.filter(
            agent=agent,
            status=Offer.Status.PENDING,
            expires_at__gt=timezone.now(),
        )
        .exclude(referral__status__in=[Referral.Status.ASSIGNED, Referral.Status.CLOSED])
        .select_related("referral", "referral__assigned_agent")
        .order_by("expires_at")
    )
    eligible_offers = []
    for offer in offers:
        if not eligibility.evaluate(agent, offer.referral)["eligible"]:
            try:
                claims.decline_offer(offer, agent)
            except claims.ClaimError:
                continue
            continue
        eligible_offers.append(offer)

    return Response(OfferSerializer(eligible_offers, many=True).data)


@api_view(["GET"])
@permission_classes([IsOpsUser])
def referral_detail(request, referral_id):
    referral = get_object_or_404(Referral, pk=referral_id)
    return Response(ReferralSerializer(referral).data)


@api_view(["POST"])
def claim_offer(request, offer_id):
    agent = _agent_for(request)
    if agent is None:
        return Response({"detail": "No agent profile."}, status=status.HTTP_403_FORBIDDEN)

    offer = get_object_or_404(Offer, pk=offer_id, agent=agent)
    try:
        claim = claims.claim_offer(offer, agent)
    except claims.ClaimError as exc:
        response_status = (
            status.HTTP_409_CONFLICT
            if exc.code == "already_claimed"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(
            {"detail": str(exc), "code": exc.code},
            status=response_status,
        )
    return Response(
        {"status": "claimed", "claim_id": claim.id, "referral_id": offer.referral_id}
    )


@api_view(["POST"])
def decline_offer(request, offer_id):
    agent = _agent_for(request)
    if agent is None:
        return Response({"detail": "No agent profile."}, status=status.HTTP_403_FORBIDDEN)

    offer = get_object_or_404(Offer, pk=offer_id, agent=agent)
    try:
        claims.decline_offer(offer, agent)
    except claims.ClaimError as exc:
        return Response(
            {"detail": str(exc), "code": exc.code}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response({"status": "declined", "offer_id": offer.id})


@api_view(["GET"])
@permission_classes([IsOpsUser])
def ops_referrals(request):
    status_filter = request.query_params.get("status")
    state_filter = request.query_params.get("state")
    min_age = request.query_params.get("min_age_minutes")
    max_age = request.query_params.get("max_age_minutes")
    if status_filter and status_filter not in Referral.Status.values:
        return Response({"detail": "Invalid status filter."}, status=status.HTTP_400_BAD_REQUEST)
    if state_filter and (len(state_filter) != 2 or not state_filter.isalpha()):
        return Response({"detail": "Invalid state filter."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        min_age_minutes = int(min_age) if min_age else None
        max_age_minutes = int(max_age) if max_age else None
    except ValueError:
        return Response({"detail": "Invalid age filter."}, status=status.HTTP_400_BAD_REQUEST)
    if (
        (min_age_minutes is not None and min_age_minutes < 0)
        or (max_age_minutes is not None and max_age_minutes < 0)
        or (
            min_age_minutes is not None
            and max_age_minutes is not None
            and min_age_minutes > max_age_minutes
        )
    ):
        return Response({"detail": "Invalid age filter."}, status=status.HTTP_400_BAD_REQUEST)

    referrals = Referral.objects.select_related("assigned_agent").prefetch_related(
        Prefetch(
            "offers",
            queryset=Offer.objects.select_related("agent").order_by("-offered_at"),
        ),
        Prefetch(
            "claims",
            queryset=Claim.objects.select_related("agent").prefetch_related(
                "notification_outbox"
            ),
        ),
    ).order_by("-created_at")
    if status_filter:
        referrals = referrals.filter(status=status_filter)
    if state_filter:
        referrals = referrals.filter(state=state_filter.upper())
    now = timezone.now()
    if min_age_minutes is not None:
        referrals = referrals.filter(
            created_at__lte=now - timedelta(minutes=min_age_minutes)
        )
    if max_age_minutes is not None:
        referrals = referrals.filter(
            created_at__gte=now - timedelta(minutes=max_age_minutes)
        )

    paginator = OpsReferralPagination()
    page = paginator.paginate_queryset(referrals, request)
    return paginator.get_paginated_response(OpsReferralSerializer(page, many=True).data)


@api_view(["GET"])
@permission_classes([IsOpsUser])
def ops_referral_events(request, referral_id):
    referral = get_object_or_404(Referral, pk=referral_id)
    return Response(ReferralEventSerializer(referral.events.all(), many=True).data)


@api_view(["POST"])
@permission_classes([IsOpsUser])
def ops_override(request, referral_id):
    """Operations reassigns (or unassigns) a referral."""
    get_object_or_404(Referral, pk=referral_id)
    serializer = OpsOverrideSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        locked_referral = Referral.objects.select_for_update().get(pk=referral_id)
        previous_agent_id = locked_referral.assigned_agent_id
        agent_id = serializer.validated_data.get("agent_id")
        note = serializer.validated_data["note"]
        active_claim_agent_ids = set(
            Claim.objects.filter(referral=locked_referral, active=True).values_list(
                "agent_id", flat=True
            )
        )
        agent_ids = {agent_id, previous_agent_id, *active_claim_agent_ids}
        agent_ids.discard(None)
        locked_agents = {
            agent.pk: agent
            for agent in Agent.objects.select_for_update().filter(pk__in=agent_ids).order_by("pk")
        }
        if agent_id is not None and agent_id not in locked_agents:
            return Response({"agent_id": ["Agent not found."]}, status=status.HTTP_400_BAD_REQUEST)
        previous_agent = locked_agents.get(previous_agent_id)
        new_agent = locked_agents.get(agent_id)

        active_claims = list(
            Claim.objects.select_for_update().filter(referral=locked_referral, active=True)
        )
        keep_claim = next(
            (claim for claim in active_claims if new_agent and claim.agent_id == new_agent.pk),
            None,
        )
        if new_agent is not None and keep_claim is None:
            new_agent.active_referral_count = Claim.objects.filter(
                agent=new_agent, active=True
            ).count()
            eligibility_result = eligibility.evaluate(new_agent, locked_referral)
            if not eligibility_result["eligible"]:
                return Response(
                    {
                        "detail": "The selected agent is not currently eligible.",
                        "reasons": eligibility_result["reasons"],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        now = timezone.now()
        Claim.objects.filter(referral=locked_referral, active=True).exclude(
            pk=keep_claim.pk if keep_claim else None
        ).update(active=False, released_at=now)
        if new_agent is not None and keep_claim is None:
            Claim.objects.create(referral=locked_referral, agent=new_agent)

        if new_agent is None:
            locked_referral.assigned_agent = None
            locked_referral.status = Referral.Status.NEW
        else:
            locked_referral.assigned_agent = new_agent
            locked_referral.status = Referral.Status.ASSIGNED

        Offer.objects.filter(
            referral=locked_referral, status=Offer.Status.PENDING
        ).update(status=Offer.Status.DECLINED, responded_at=now)
        locked_referral.save(update_fields=["assigned_agent", "status"])

        for locked_agent in locked_agents.values():
            locked_agent.active_referral_count = Claim.objects.filter(
                agent=locked_agent, active=True
            ).count()
            locked_agent.save(update_fields=["active_referral_count"])

        record_event(
            locked_referral,
            ReferralEvent.Type.OPS_OVERRIDE,
            actor=request.user.get_username(),
            payload={
                "previous_agent_id": previous_agent.id if previous_agent else None,
                "new_agent_id": new_agent.id if new_agent else None,
                "note": note,
            },
        )
        if new_agent is None:
            matching.advance_referral(locked_referral.pk)

    return Response({"status": "overridden", "referral_id": locked_referral.id})
