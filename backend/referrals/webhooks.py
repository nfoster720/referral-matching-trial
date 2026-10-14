"""Intake webhook for referrals delivered by the upstream lead platform."""

import hmac
import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from . import matching
from .events import record_event
from .models import ProcessedWebhookEvent, Referral, ReferralEvent

logger = logging.getLogger("referrals.webhooks")


class ReferralWebhookSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=128)
    external_id = serializers.CharField(max_length=64)
    customer_name = serializers.CharField(max_length=120)
    customer_email = serializers.EmailField(max_length=254)
    customer_phone = serializers.CharField(max_length=40)
    state = serializers.RegexField(r"^[A-Z]{2}$", max_length=2)
    postal_code = serializers.RegexField(r"^\d{5}(?:-\d{4})?$", max_length=10)
    estimated_price = serializers.IntegerField(min_value=1, max_value=2_147_483_647)
    intent_level = serializers.ChoiceField(
        choices=Referral.Intent.choices, required=False, default=Referral.Intent.WARM
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
def intake_referral(request):
    supplied_token = request.headers.get("X-Webhook-Token", "")
    if not hmac.compare_digest(supplied_token, settings.WEBHOOK_TOKEN):
        return Response({"detail": "invalid webhook token"}, status=status.HTTP_403_FORBIDDEN)

    serializer = ReferralWebhookSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {"detail": "invalid webhook payload", "fields": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    payload = serializer.validated_data
    event_id = payload["event_id"]
    external_id = payload["external_id"]

    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    ProcessedWebhookEvent.objects.create(event_id=event_id)
            except IntegrityError:
                logger.info(
                    "duplicate event delivery ignored: event_id=%s external_id=%s",
                    event_id,
                    external_id,
                )
                return Response(
                    {
                        "status": "duplicate_ignored",
                        "event_id": event_id,
                        "external_id": external_id,
                    }
                )

            try:
                referral = Referral.objects.get(external_id=external_id)
            except Referral.DoesNotExist:
                referral = Referral.objects.create(
                    external_id=external_id,
                    customer_name=payload["customer_name"],
                    customer_email=payload["customer_email"],
                    customer_phone=payload["customer_phone"],
                    state=payload["state"],
                    postal_code=payload["postal_code"],
                    estimated_price=payload["estimated_price"],
                    intent_level=payload["intent_level"],
                )
                record_event(
                    referral,
                    ReferralEvent.Type.REFERRAL_RECEIVED,
                    actor="intake-webhook",
                    payload={"event_id": event_id},
                )
                offers = matching.build_offers(referral)
                logger.info(
                    "referral ingested: event_id=%s external_id=%s offers=%d",
                    event_id,
                    referral.external_id,
                    len(offers),
                )
                return Response(
                    {"status": "created", "referral_id": referral.id, "offers": len(offers)},
                    status=status.HTTP_201_CREATED,
                )

            logger.info(
                "duplicate lead delivery ignored: event_id=%s external_id=%s",
                event_id,
                external_id,
            )
            return Response(
                {
                    "status": "duplicate_ignored",
                    "event_id": event_id,
                    "external_id": external_id,
                    "referral_id": referral.id,
                }
            )
    except IntegrityError:
        referral = Referral.objects.filter(external_id=external_id).first()
        if referral is None:
            raise
        try:
            with transaction.atomic():
                ProcessedWebhookEvent.objects.create(event_id=event_id)
        except IntegrityError:
            logger.info("duplicate event delivery ignored after lead race: event_id=%s", event_id)
            return Response(
                {
                    "status": "duplicate_ignored",
                    "event_id": event_id,
                    "external_id": external_id,
                    "referral_id": referral.id,
                }
            )
        logger.info(
            "concurrent duplicate lead delivery ignored: event_id=%s external_id=%s",
            event_id,
            external_id,
        )
        return Response(
            {
                "status": "duplicate_ignored",
                "event_id": event_id,
                "external_id": external_id,
                "referral_id": referral.id,
            }
        )
