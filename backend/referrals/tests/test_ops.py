from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from referrals.models import Claim, Offer, Referral, ReferralEvent

pytestmark = pytest.mark.django_db


def test_dashboard_requires_ops_role(make_agent, client_for):
    agent = make_agent()
    response = client_for(agent.user).get("/api/ops/referrals/")
    assert response.status_code == 403


@override_settings(DEBUG=False)
def test_development_identity_tokens_are_hidden_when_debug_is_disabled(client_for, make_user):
    user = make_user("ops.test", is_staff=True)
    response = client_for(user).get("/api/identities/")
    assert response.status_code == 404


def test_dashboard_lists_referrals(make_user, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    make_referral()
    response = client_for(ops).get("/api/ops/referrals/")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert len(response.data["results"]) == 1
    assert response.data["results"][0]["external_id"] == "lead-test-1"
    assert "events" not in response.data["results"][0]


def test_dashboard_filters_and_paginates(make_user, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    for index in range(55):
        make_referral(
            external_id=f"lead-page-{index}",
            state="CO" if index < 53 else "TX",
        )

    client = client_for(ops)
    first_page = client.get("/api/ops/referrals/?limit=50&offset=0&state=CO")
    second_page = client.get("/api/ops/referrals/?limit=50&offset=50&state=CO")
    filtered = client.get("/api/ops/referrals/?state=TX&min_age_minutes=0")

    assert first_page.data["count"] == 53
    assert len(first_page.data["results"]) == 50
    assert first_page.data["next"] is not None
    assert len(second_page.data["results"]) == 3
    assert [row["state"] for row in filtered.data["results"]] == ["TX", "TX"]


def test_dashboard_rejects_invalid_age_filter(make_user, client_for):
    ops = make_user("ops.test", is_staff=True)
    response = client_for(ops).get("/api/ops/referrals/?min_age_minutes=abc")
    assert response.status_code == 400


def test_override_reassigns_and_records_event(make_user, make_agent, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    original = make_agent("agent.original")
    replacement = make_agent("agent.replacement")
    referral = make_referral(status=Referral.Status.ASSIGNED, assigned_agent=original)
    Claim.objects.create(referral=referral, agent=original)

    response = client_for(ops).post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"agent_id": replacement.id, "note": "customer requested"},
        format="json",
    )
    assert response.status_code == 200

    referral.refresh_from_db()
    assert referral.assigned_agent_id == replacement.id
    assert not Claim.objects.filter(referral=referral, agent=original, active=True).exists()
    assert Claim.objects.filter(referral=referral, agent=replacement, active=True).exists()
    assert referral.events.filter(event_type=ReferralEvent.Type.OPS_OVERRIDE).exists()


def test_override_rejects_unknown_or_ineligible_agent(
    make_user, make_agent, make_referral, client_for
):
    ops = make_user("ops.test", is_staff=True)
    inactive = make_agent("agent.inactive", active=False)
    referral = make_referral()
    client = client_for(ops)

    missing = client.post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"agent_id": 99999, "note": "manual review"},
        format="json",
    )
    ineligible = client.post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"agent_id": inactive.id, "note": "manual review"},
        format="json",
    )

    assert missing.status_code == 400
    assert ineligible.status_code == 400
    assert referral.claims.count() == 0


def test_override_requires_a_note(make_user, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    referral = make_referral()

    response = client_for(ops).post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"agent_id": None, "note": ""},
        format="json",
    )

    assert response.status_code == 400
    assert "note" in response.data


def test_override_requires_explicit_agent_id(make_user, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    referral = make_referral()

    response = client_for(ops).post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"note": "should not silently unassign"},
        format="json",
    )

    assert response.status_code == 400
    assert "agent_id" in response.data
    assert referral.assigned_agent_id is None


def test_explicit_null_agent_id_unassigns(make_user, make_agent, make_referral, client_for):
    ops = make_user("ops.test", is_staff=True)
    assigned_agent = make_agent("agent.assigned")
    referral = make_referral(status=Referral.Status.ASSIGNED, assigned_agent=assigned_agent)
    active_claim = Claim.objects.create(referral=referral, agent=assigned_agent)

    response = client_for(ops).post(
        f"/api/ops/referrals/{referral.id}/override/",
        {"agent_id": None, "note": "Customer requested reassignment"},
        format="json",
    )

    assert response.status_code == 200
    referral.refresh_from_db()
    active_claim.refresh_from_db()
    assigned_agent.refresh_from_db()
    assert referral.assigned_agent_id is None
    assert referral.status in (Referral.Status.NEW, Referral.Status.OFFERED)
    assert active_claim.active is False
    assert assigned_agent.active_referral_count == 0


def _concurrent_override(referral_id, agent_id, token, barrier):
    close_old_connections()
    try:
        barrier.wait(timeout=10)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        response = client.post(
            f"/api/ops/referrals/{referral_id}/override/",
            {"agent_id": agent_id, "note": "concurrent ops reassignment"},
            format="json",
        )
        return response.status_code
    finally:
        close_old_connections()


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Requires PostgreSQL row locks.",
)
def test_concurrent_overrides_keep_claim_assignment_and_capacity_consistent(
    make_user, make_agent, make_referral
):
    ops = make_user("ops.test", is_staff=True)
    agents = [make_agent("agent.ops.first"), make_agent("agent.ops.second")]
    referral = make_referral()
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _concurrent_override,
                referral.pk,
                agent.pk,
                ops.auth_token.key,
                barrier,
            )
            for agent in agents
        ]
        statuses = [future.result(timeout=20) for future in futures]

    assert statuses == [200, 200]
    referral.refresh_from_db()
    active_claims = list(Claim.objects.filter(referral=referral, active=True))
    assert len(active_claims) == 1
    assert active_claims[0].agent_id == referral.assigned_agent_id
    for agent in agents:
        agent.refresh_from_db()
        assert agent.active_referral_count == Claim.objects.filter(
            agent=agent, active=True
        ).count()


def test_dashboard_offer_prefetch_query_count_does_not_grow_with_rows(
    make_user, make_agent, make_referral, client_for
):
    ops = make_user("ops.test", is_staff=True)
    agent = make_agent()
    for index in range(8):
        referral = make_referral(external_id=f"lead-dashboard-{index}")
        Offer.objects.create(
            referral=referral,
            agent=agent,
            score=0.9,
            eligibility_snapshot={"eligible": True, "reasons": []},
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    with CaptureQueriesContext(connection) as queries:
        response = client_for(ops).get("/api/ops/referrals/")

    assert response.status_code == 200
    assert len(response.data["results"]) == 8
    assert len(queries) <= 6
