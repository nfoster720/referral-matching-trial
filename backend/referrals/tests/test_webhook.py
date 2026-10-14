from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.conf import settings
from django.db import IntegrityError, OperationalError, close_old_connections, connection
from rest_framework.test import APIClient

from referrals.models import Offer, ProcessedWebhookEvent, Referral, ReferralEvent

pytestmark = pytest.mark.django_db

PAYLOAD = {
    "event_id": "evt-abc-123",
    "external_id": "lead-90001",
    "customer_name": "Jordan Sample",
    "customer_email": "jordan.sample@example.com",
    "customer_phone": "555-0101",
    "state": "CO",
    "postal_code": "80202",
    "estimated_price": 450_000,
    "intent_level": "hot",
}


def post_webhook(payload, token="dev-webhook-secret"):
    client = APIClient()
    return client.post(
        "/api/webhooks/referrals/", payload, format="json", headers={"X-Webhook-Token": token}
    )


def test_webhook_requires_token(db):
    response = post_webhook(PAYLOAD, token="wrong")
    assert response.status_code == 403
    assert Referral.objects.count() == 0


def test_webhook_rejects_incomplete_payload(db):
    response = post_webhook({"event_id": "evt-1"})
    assert response.status_code == 400
    assert "external_id" in response.data["fields"]


def test_duplicate_webhook_delivery_is_ignored(db):
    response = post_webhook(PAYLOAD, token="dev-webhook-secret")
    assert response.status_code == 201

    duplicate = post_webhook(PAYLOAD, token="dev-webhook-secret")
    assert duplicate.status_code == 200
    assert duplicate.data["status"] == "duplicate_ignored"
    assert Referral.objects.filter(external_id=PAYLOAD["external_id"]).count() == 1


def test_same_external_lead_with_new_event_id_is_ignored(db):
    created = post_webhook(PAYLOAD)
    retry = post_webhook(dict(PAYLOAD, event_id="evt-second-delivery"))

    assert created.status_code == 201
    assert retry.status_code == 200
    assert retry.data["status"] == "duplicate_ignored"
    assert Referral.objects.filter(external_id=PAYLOAD["external_id"]).count() == 1
    assert ProcessedWebhookEvent.objects.filter(event_id="evt-second-delivery").exists()


def test_event_id_losing_external_id_race_is_recorded_and_retry_is_ignored(
    db, monkeypatch
):
    initial = post_webhook(PAYLOAD)
    assert initial.status_code == 201

    original_get = Referral.objects.get
    get_calls = 0

    def race_get(*args, **kwargs):
        nonlocal get_calls
        get_calls += 1
        if get_calls == 1:
            raise Referral.DoesNotExist
        return original_get(*args, **kwargs)

    def race_create(**kwargs):
        raise IntegrityError("concurrent unique external_id insert")

    monkeypatch.setattr(Referral.objects, "get", race_get)
    monkeypatch.setattr(Referral.objects, "create", race_create)
    raced_payload = dict(PAYLOAD, event_id="evt-lost-race")

    raced = post_webhook(raced_payload)
    assert raced.status_code == 200
    assert ProcessedWebhookEvent.objects.filter(event_id="evt-lost-race").exists()

    retried = post_webhook(raced_payload)
    assert retried.status_code == 200
    assert retried.data["status"] == "duplicate_ignored"
    assert Referral.objects.filter(external_id=PAYLOAD["external_id"]).count() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"estimated_price": "not-a-price"},
        {"intent_level": "urgent"},
        {"customer_email": "invalid"},
        {"state": "Colorado"},
        {"postal_code": "not-a-zip"},
    ],
)
def test_webhook_rejects_malformed_fields(db, overrides):
    response = post_webhook(dict(PAYLOAD, **overrides))
    assert response.status_code == 400
    assert response.data["detail"] == "invalid webhook payload"
    assert Referral.objects.count() == 0


def test_webhook_offer_generation_failure_rolls_back_all_writes(db, monkeypatch):
    def fail(_referral):
        raise OperationalError("test offer generation failure")

    monkeypatch.setattr("referrals.webhooks.matching.build_offers", fail)
    with pytest.raises(OperationalError):
        post_webhook(PAYLOAD)

    assert Referral.objects.count() == 0
    assert ProcessedWebhookEvent.objects.count() == 0


def _concurrent_webhook(payload, barrier):
    close_old_connections()
    try:
        barrier.wait(timeout=10)
        response = post_webhook(payload, token=settings.WEBHOOK_TOKEN)
        return response.status_code
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Requires PostgreSQL concurrency semantics.",
)
@pytest.mark.parametrize("same_event", [True, False])
def test_concurrent_duplicate_webhook_delivery_is_idempotent(same_event):
    event_ids = (
        ("evt-concurrent", "evt-concurrent")
        if same_event
        else ("evt-concurrent-first", "evt-concurrent-second")
    )
    payloads = [
        dict(PAYLOAD, event_id=event_id)
        for event_id in event_ids
    ]
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_concurrent_webhook, payload, barrier)
            for payload in payloads
        ]
        statuses = [future.result(timeout=20) for future in futures]

    assert statuses.count(201) == 1
    assert statuses.count(200) == 1
    assert Referral.objects.filter(external_id=PAYLOAD["external_id"]).count() == 1
    expected_events = 1 if same_event else 2
    assert ProcessedWebhookEvent.objects.filter(
        event_id__in=set(event_ids)
    ).count() == expected_events


def test_webhook_creates_referral_and_offers(make_agent):
    make_agent("agent.one", performance_score=0.9)
    make_agent("agent.two", performance_score=0.6)

    response = post_webhook(dict(PAYLOAD, event_id="evt-create-1"))
    assert response.status_code == 201

    referral = Referral.objects.get(external_id="lead-90001")
    assert referral.status == Referral.Status.OFFERED
    assert referral.customer_name == "Jordan Sample"
    assert Offer.objects.filter(referral=referral).count() > 0
    event_types = set(referral.events.values_list("event_type", flat=True))
    assert ReferralEvent.Type.REFERRAL_RECEIVED in event_types
    assert ReferralEvent.Type.OFFER_SENT in event_types
