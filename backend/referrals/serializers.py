from rest_framework import serializers

from .models import Agent, Claim, NotificationOutbox, Offer, Referral, ReferralEvent


class AgentSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["id", "name", "email", "performance_score", "active_referral_count", "capacity"]


class ReferralSerializer(serializers.ModelSerializer):
    assigned_agent = AgentSummarySerializer(read_only=True)

    class Meta:
        model = Referral
        fields = [
            "id",
            "external_id",
            "customer_name",
            "customer_email",
            "customer_phone",
            "state",
            "postal_code",
            "estimated_price",
            "intent_level",
            "status",
            "assigned_agent",
            "created_at",
        ]


class OfferSerializer(serializers.ModelSerializer):
    referral = ReferralSerializer(read_only=True)

    class Meta:
        model = Offer
        fields = [
            "id",
            "referral",
            "score",
            "score_explanation",
            "eligibility_snapshot",
            "status",
            "offered_at",
            "expires_at",
            "responded_at",
        ]


class ReferralEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralEvent
        fields = ["id", "event_type", "actor", "payload", "created_at"]


class ClaimSerializer(serializers.ModelSerializer):
    agent = AgentSummarySerializer(read_only=True)

    class Meta:
        model = Claim
        fields = ["id", "agent", "active", "created_at", "released_at"]


class OpsReferralSerializer(serializers.ModelSerializer):
    assigned_agent = serializers.SerializerMethodField()
    offers = serializers.SerializerMethodField()
    notifications = serializers.SerializerMethodField()
    age_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Referral
        fields = [
            "id",
            "external_id",
            "customer_name",
            "state",
            "postal_code",
            "estimated_price",
            "intent_level",
            "status",
            "assigned_agent",
            "age_minutes",
            "offers",
            "notifications",
            "created_at",
        ]

    def get_assigned_agent(self, obj):
        if obj.assigned_agent is None:
            return None
        return {
            "id": obj.assigned_agent.id,
            "name": obj.assigned_agent.name,
            "email": obj.assigned_agent.email,
        }

    def get_offers(self, obj):
        results = []
        for offer in obj.offers.all():
            results.append(
                {
                    "id": offer.id,
                    "agent": offer.agent.name,
                    "score": offer.score,
                    "status": offer.status,
                    "offered_at": offer.offered_at,
                    "expires_at": offer.expires_at,
                }
            )
        return results

    def get_notifications(self, obj):
        notifications = []
        for claim in obj.claims.all():
            try:
                outbox = claim.notification_outbox
            except NotificationOutbox.DoesNotExist:
                continue
            notifications.append(
                {
                    "agent": claim.agent.name,
                    "status": outbox.status,
                    "attempts": outbox.attempts,
                    "last_error_code": outbox.last_error_code,
                    "next_attempt_at": outbox.next_attempt_at,
                }
            )
        return notifications

    def get_age_minutes(self, obj):
        from django.utils import timezone

        return int((timezone.now() - obj.created_at).total_seconds() // 60)
