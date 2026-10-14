from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rest_framework.test import APIClient

from referrals import matching
from referrals.models import Claim, NotificationOutbox, Offer, Referral, ReferralEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def pending_offer(make_agent, make_referral):
    agent = make_agent()
    referral = make_referral()
    return Offer.objects.create(
        referral=referral,
        agent=agent,
        score=0.9,
        eligibility_snapshot={"eligible": True, "reasons": []},
        expires_at=timezone.now() + timedelta(minutes=10),
    )


def test_agent_can_claim_pending_offer(pending_offer, client_for):
    client = client_for(pending_offer.agent.user)
    response = client.post(f"/api/offers/{pending_offer.id}/claim/")
    assert response.status_code == 200

    referral = Referral.objects.get(pk=pending_offer.referral_id)
    assert referral.status == Referral.Status.ASSIGNED
    assert referral.assigned_agent_id == pending_offer.agent_id
    assert Claim.objects.filter(referral=referral, agent=pending_offer.agent, active=True).exists()

    pending_offer.refresh_from_db()
    assert pending_offer.status == Offer.Status.ACCEPTED
    assert referral.events.filter(event_type=ReferralEvent.Type.CLAIM_SUCCEEDED).exists()
    assert NotificationOutbox.objects.filter(
        claim__referral=referral, status=NotificationOutbox.Status.PENDING
    ).exists()


def test_agent_cannot_claim_expired_offer(pending_offer, client_for):
    Offer.objects.filter(pk=pending_offer.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    client = client_for(pending_offer.agent.user)
    response = client.post(f"/api/offers/{pending_offer.id}/claim/")
    assert response.status_code == 400
    assert response.data["code"] == "offer_expired"


def test_only_one_claim_can_be_active_for_same_referral(make_agent, make_referral, client_for):
    referral = make_referral()
    first_agent = make_agent("agent.first")
    second_agent = make_agent("agent.second")
    first_offer = Offer.objects.create(
        referral=referral,
        agent=first_agent,
        score=0.9,
        eligibility_snapshot={"eligible": True, "reasons": []},
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    second_offer = Offer.objects.create(
        referral=referral,
        agent=second_agent,
        score=0.8,
        eligibility_snapshot={"eligible": True, "reasons": []},
        expires_at=timezone.now() + timedelta(minutes=10),
    )

    first_client = client_for(first_agent.user)
    first_response = first_client.post(f"/api/offers/{first_offer.id}/claim/")
    assert first_response.status_code == 200

    second_client = client_for(second_agent.user)
    second_response = second_client.post(f"/api/offers/{second_offer.id}/claim/")
    assert second_response.status_code == 409
    assert Claim.objects.filter(referral=referral, active=True).count() == 1


def test_claim_rechecks_agent_capacity(pending_offer, make_referral, client_for):
    other_referral = make_referral(external_id="lead-capacity")
    Claim.objects.create(referral=other_referral, agent=pending_offer.agent)
    pending_offer.agent.capacity = 1
    pending_offer.agent.active_referral_count = 1
    pending_offer.agent.save(update_fields=["capacity", "active_referral_count"])

    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )

    assert response.status_code == 400
    assert response.data["code"] == "agent_ineligible"
    assert not Claim.objects.filter(referral=pending_offer.referral, active=True).exists()


def test_claim_rechecks_agent_eligibility(pending_offer, client_for):
    pending_offer.agent.suspended = True
    pending_offer.agent.save(update_fields=["suspended"])

    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )

    assert response.status_code == 400
    assert response.data["code"] == "agent_ineligible"


def test_claim_revokes_other_pending_offers(pending_offer, make_agent, client_for):
    other_agent = make_agent("agent.second")
    other_offer = Offer.objects.create(
        referral=pending_offer.referral,
        agent=other_agent,
        score=0.8,
        eligibility_snapshot={"eligible": True, "reasons": []},
        expires_at=timezone.now() + timedelta(minutes=10),
    )

    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )

    assert response.status_code == 200
    other_offer.refresh_from_db()
    assert other_offer.status == Offer.Status.DECLINED
    assert client_for(other_agent.user).get("/api/offers/mine/").data == []


def test_notification_failure_does_not_undo_claim(pending_offer, client_for):
    Referral.objects.filter(pk=pending_offer.referral_id).update(
        customer_phone="555-0157 ext. TBD"
    )

    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )

    assert response.status_code == 200
    assert Claim.objects.filter(referral_id=pending_offer.referral_id, active=True).exists()
    assert NotificationOutbox.objects.filter(
        claim__referral_id=pending_offer.referral_id,
        status=NotificationOutbox.Status.PENDING,
    ).exists()


def test_notification_command_records_safe_retry_state(pending_offer, client_for):
    Referral.objects.filter(pk=pending_offer.referral_id).update(
        customer_phone="555-0157 ext. TBD"
    )
    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )
    assert response.status_code == 200

    call_command("send_pending_notifications")
    outbox = NotificationOutbox.objects.get(claim__referral_id=pending_offer.referral_id)

    assert outbox.attempts == 1
    assert outbox.status == NotificationOutbox.Status.PENDING
    assert outbox.last_error_code == "delivery_failed"
    assert outbox.lease_expires_at is None
    assert outbox.lease_token is None


@pytest.mark.django_db(transaction=True)
def test_notification_gateway_runs_outside_transaction_and_uses_stable_key(
    pending_offer, client_for, monkeypatch
):
    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )
    assert response.status_code == 200
    outbox = NotificationOutbox.objects.get(claim__referral_id=pending_offer.referral_id)
    observed = {}

    def send(claim, idempotency_key=None):
        observed["in_atomic_block"] = connections["default"].in_atomic_block
        observed["key"] = idempotency_key
        observed["claim_id"] = claim.pk

    monkeypatch.setattr(
        "referrals.management.commands.send_pending_notifications.notifications.send_claim_confirmation",
        send,
    )
    call_command("send_pending_notifications")
    outbox.refresh_from_db()

    assert observed == {
        "in_atomic_block": False,
        "key": f"claim-confirmation-{outbox.claim_id}",
        "claim_id": outbox.claim_id,
    }
    assert outbox.status == NotificationOutbox.Status.SENT
    assert outbox.attempts == 1
    assert outbox.lease_token is None


def test_notification_worker_marks_retry_limit_as_terminal(
    pending_offer, client_for
):
    Referral.objects.filter(pk=pending_offer.referral_id).update(
        customer_phone="555-0157 ext. TBD"
    )
    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )
    assert response.status_code == 200
    outbox = NotificationOutbox.objects.get(claim__referral_id=pending_offer.referral_id)
    outbox.attempts = 7
    outbox.save(update_fields=["attempts"])

    call_command("send_pending_notifications")
    outbox.refresh_from_db()

    assert outbox.status == NotificationOutbox.Status.FAILED
    assert outbox.attempts == 8
    assert outbox.last_error_code == "delivery_failed"


def test_notification_worker_recovers_expired_lease(pending_offer, client_for, monkeypatch):
    response = client_for(pending_offer.agent.user).post(
        f"/api/offers/{pending_offer.id}/claim/"
    )
    assert response.status_code == 200
    outbox = NotificationOutbox.objects.get(claim__referral_id=pending_offer.referral_id)
    outbox.status = NotificationOutbox.Status.PROCESSING
    outbox.attempts = 1
    outbox.lease_token = uuid4()
    outbox.lease_expires_at = timezone.now() - timedelta(minutes=1)
    outbox.save(
        update_fields=["status", "attempts", "lease_token", "lease_expires_at"]
    )
    monkeypatch.setattr(
        "referrals.management.commands.send_pending_notifications.notifications.send_claim_confirmation",
        lambda claim, idempotency_key=None: None,
    )

    call_command("send_pending_notifications")
    outbox.refresh_from_db()

    assert outbox.status == NotificationOutbox.Status.SENT
    assert outbox.attempts == 2
    assert outbox.lease_token is None


def test_expiration_command_advances_stalled_referral(pending_offer):
    Offer.objects.filter(pk=pending_offer.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    call_command("expire_offers")

    pending_offer.refresh_from_db()
    pending_offer.referral.refresh_from_db()
    assert pending_offer.status == Offer.Status.EXPIRED
    assert pending_offer.referral.status == Referral.Status.NEW
    assert pending_offer.referral.events.filter(
        event_type=ReferralEvent.Type.OFFER_EXPIRED
    ).exists()


def test_agent_cannot_access_referral_detail(
    pending_offer, client_for
):
    client = client_for(pending_offer.agent.user)

    response = client.get(
        f"/api/referrals/{pending_offer.referral_id}/"
    )

    assert response.status_code == 403


def test_agent_cannot_claim_someone_elses_offer(pending_offer, make_agent, client_for):
    other = make_agent("agent.other")
    client = client_for(other.user)
    response = client.post(f"/api/offers/{pending_offer.id}/claim/")
    assert response.status_code == 404


def test_agent_can_decline_offer(pending_offer, client_for):
    client = client_for(pending_offer.agent.user)
    response = client.post(f"/api/offers/{pending_offer.id}/decline/")
    assert response.status_code == 200

    pending_offer.refresh_from_db()
    assert pending_offer.status == Offer.Status.DECLINED
    assert pending_offer.referral.events.filter(
        event_type=ReferralEvent.Type.OFFER_DECLINED
    ).exists()
    pending_offer.referral.refresh_from_db()
    assert pending_offer.referral.status == Referral.Status.NEW


def test_queue_lists_pending_offers(pending_offer, client_for):
    client = client_for(pending_offer.agent.user)
    response = client.get("/api/offers/mine/")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["referral"]["external_id"] == pending_offer.referral.external_id


def _concurrent_claim(offer_id, token, barrier):
    close_old_connections()
    try:
        barrier.wait(timeout=10)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        response = client.post(f"/api/offers/{offer_id}/claim/")
        return response.status_code
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL row locks.")
def test_concurrent_claims_cannot_overbook_one_agent(make_agent, make_referral):
    agent = make_agent(capacity=1)
    offers = []
    for index in range(2):
        referral = make_referral(external_id=f"lead-concurrent-{index}")
        offers.append(
            Offer.objects.create(
                referral=referral,
                agent=agent,
                score=0.9,
                eligibility_snapshot={"eligible": True, "reasons": []},
                expires_at=timezone.now() + timedelta(minutes=10),
            )
        )

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_concurrent_claim, offer.id, agent.user.auth_token.key, barrier)
            for offer in offers
        ]
        statuses = [future.result(timeout=20) for future in futures]

    assert statuses.count(200) == 1
    assert Claim.objects.filter(agent=agent, active=True).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL row locks.")
def test_concurrent_claims_on_same_referral_create_one_owner(
    make_agent, make_referral
):
    referral = make_referral()
    agents = [make_agent("agent.concurrent.first"), make_agent("agent.concurrent.second")]
    offers = [
        Offer.objects.create(
            referral=referral,
            agent=agent,
            score=0.9,
            eligibility_snapshot={"eligible": True, "reasons": []},
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        for agent in agents
    ]

    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_claim,
                offer.id,
                agent.user.auth_token.key,
                barrier,
            )
            for offer, agent in zip(offers, agents)
        ]
        statuses = [future.result(timeout=20) for future in futures]

    assert statuses.count(200) == 1
    assert statuses.count(409) == 1
    assert Claim.objects.filter(referral=referral, active=True).count() == 1


def _concurrent_matching(referral_id, barrier):
    close_old_connections()
    try:
        referral = Referral.objects.get(pk=referral_id)
        offers = matching.build_offers(referral)
        return [offer.pk for offer in offers]
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Requires PostgreSQL concurrency semantics.",
)
def test_simultaneous_matching_does_not_reserve_last_slot_but_claim_rechecks(
    make_agent, make_referral, client_for, monkeypatch
):
    agent = make_agent(capacity=1, active_referral_count=0)
    referrals = [
        make_referral(external_id=f"lead-matching-capacity-{index}")
        for index in range(2)
    ]
    barrier = Barrier(2)
    original_score_agent = matching.score_agent

    def rendezvous_score(candidate, referral):
        result = original_score_agent(candidate, referral)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(matching, "score_agent", rendezvous_score)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_concurrent_matching, referral.pk, barrier)
            for referral in referrals
        ]
        offer_ids = [
            offer_id
            for future in futures
            for offer_id in future.result(timeout=20)
        ]

    assert len(offer_ids) == 2
    offers = list(Offer.objects.filter(pk__in=offer_ids).order_by("pk"))
    responses = [
        client_for(agent.user).post(f"/api/offers/{offer.pk}/claim/")
        for offer in offers
    ]
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert Claim.objects.filter(agent=agent, active=True).count() == 1
