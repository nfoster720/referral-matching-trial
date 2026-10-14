"""Deterministic seed data for the referral matching pilot.

All people, emails, and phone numbers are synthetic. Phone numbers use the
reserved 555-01xx fictional range. Re-running with the same --seed produces the
same dataset.
"""

import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from rest_framework.authtoken.models import Token

from referrals import matching
from referrals.events import record_event
from referrals.models import (
    Agent,
    Claim,
    NotificationOutbox,
    Offer,
    ProcessedWebhookEvent,
    Referral,
    ReferralEvent,
)

FIRST_NAMES = [
    "Avery", "Blake", "Casey", "Dakota", "Emerson", "Finley", "Harper", "Jordan",
    "Kendall", "Logan", "Morgan", "Parker", "Quinn", "Reese", "Rowan", "Sawyer",
    "Skyler", "Taylor",
]
LAST_NAMES = [
    "Example", "Sample", "Testerson", "Placeholder", "Fakerly", "Mockwell",
    "Demoson", "Stubbins", "Fixture", "Seedman",
]

MARKETS = {
    "CO": {"zips": ["802", "803", "804"], "city": "Denver"},
    "TX": {"zips": ["750", "751", "752"], "city": "Dallas"},
    "GA": {"zips": ["303", "300"], "city": "Atlanta"},
    "FL": {"zips": ["331", "328"], "city": "Miami/Orlando"},
    "AZ": {"zips": ["850", "852"], "city": "Phoenix"},
}


class Command(BaseCommand):
    help = "Seed deterministic trial data (agents, referrals, offers, events)."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=1042)
        parser.add_argument("--history", type=int, default=300,
                            help="Older referrals to seed for dashboard history")

    def handle(self, *args, **options):
        rng = random.Random(options["seed"])
        now = timezone.now()

        self.stdout.write("Clearing existing trial data...")
        ReferralEvent.objects.all().delete()
        NotificationOutbox.objects.all().delete()
        Claim.objects.all().delete()
        Offer.objects.all().delete()
        Referral.objects.all().delete()
        ProcessedWebhookEvent.objects.all().delete()
        Agent.objects.all().delete()
        Token.objects.all().delete()
        User.objects.exclude(is_superuser=True).delete()

        ops_user = self._make_user("ops.riley", "Riley Ops", is_staff=True)
        agents = self._seed_agents(rng)
        self._seed_pilot_referrals(rng, agents, now)
        self._seed_history(rng, agents, now, options["history"])
        for agent in Agent.objects.all():
            agent.active_referral_count = Claim.objects.filter(agent=agent, active=True).count()
            agent.save(update_fields=["active_referral_count"])

        self.stdout.write(self.style.SUCCESS("\nSeed complete. Sign-in identities:"))
        self.stdout.write(f"  {'username':<22}{'role':<8}token")
        for user in User.objects.filter(auth_token__isnull=False).order_by("username"):
            role = "ops" if user.is_staff else "agent"
            self.stdout.write(f"  {user.username:<22}{role:<8}{user.auth_token.key}")
        self.stdout.write(
            f"\nAgents: {Agent.objects.count()}  Referrals: {Referral.objects.count()}  "
            f"Offers: {Offer.objects.count()}  Events: {ReferralEvent.objects.count()}"
        )
        assert ops_user  # keep linters honest

    # -- helpers -----------------------------------------------------------

    def _make_user(self, username, display_name, is_staff=False):
        first, _, last = display_name.partition(" ")
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="trial-pass",
            first_name=first,
            last_name=last,
            is_staff=is_staff,
        )
        Token.objects.create(user=user, key=f"tok-{username}"[:40])
        return user

    def _make_agent(self, username, name, **kwargs):
        user = self._make_user(username, name)
        defaults = dict(
            active=True,
            suspended=False,
            licensed_states=["CO"],
            service_area_zip_prefixes=["802"],
            minimum_price=200_000,
            maximum_price=1_100_000,
            capacity=5,
            active_referral_count=1,
            performance_score=0.7,
        )
        defaults.update(kwargs)
        return Agent.objects.create(user=user, name=name, email=user.email, **defaults)

    def _seed_agents(self, rng):
        agents = {}
        # Denver market — the pilot's launch market, with every archetype present.
        agents["carol"] = self._make_agent(
            "agent.carol", "Carol Fixture",
            performance_score=0.92, active_referral_count=2,
            service_area_zip_prefixes=["802", "803"],
        )
        agents["devon"] = self._make_agent(
            "agent.devon", "Devon Sample",
            performance_score=0.78, service_area_zip_prefixes=["802", "803", "804"],
        )
        agents["noor"] = self._make_agent(
            "agent.noor", "Noor Placeholder",
            performance_score=None, active_referral_count=0,
            service_area_zip_prefixes=["802", "804"],
        )
        agents["hank"] = self._make_agent(
            "agent.hank", "Hank Atcapacity",
            performance_score=0.85, capacity=3, active_referral_count=3,
            service_area_zip_prefixes=["803", "804"],
        )
        agents["sabine"] = self._make_agent(
            "agent.sabine", "Sabine Suspendik",
            suspended=True, performance_score=None, active_referral_count=0,
        )
        agents["ivan"] = self._make_agent(
            "agent.ivan", "Ivan Inactivov",
            active=False, performance_score=0.9,
        )
        agents["lux"] = self._make_agent(
            "agent.lux", "Lux Uppermarket",
            minimum_price=900_000, maximum_price=3_000_000, performance_score=0.88,
            service_area_zip_prefixes=["802", "803", "804"],
        )
        # Out-of-market agents.
        agents["tessa"] = self._make_agent(
            "agent.tessa", "Tessa Texworth",
            licensed_states=["TX"], service_area_zip_prefixes=["750", "751", "752"],
            performance_score=0.81,
        )
        agents["gary"] = self._make_agent(
            "agent.gary", "Gary Georgison",
            licensed_states=["GA"], service_area_zip_prefixes=["303", "300"],
            performance_score=0.66,
        )
        agents["fern"] = self._make_agent(
            "agent.fern", "Fern Floridian",
            licensed_states=["FL"], service_area_zip_prefixes=["331", "328"],
            performance_score=None,
        )
        agents["ash"] = self._make_agent(
            "agent.ash", "Ash Arizonan",
            licensed_states=["AZ"], service_area_zip_prefixes=["850", "852"],
            performance_score=0.74,
        )
        agents["monty"] = self._make_agent(
            "agent.monty", "Monty Multistate",
            licensed_states=["CO", "TX"], service_area_zip_prefixes=["802", "803", "750"],
            performance_score=0.55, capacity=8,
        )
        agents["dell"] = self._make_agent(
            "agent.dell", "Dell Dallasworth",
            licensed_states=["TX"], service_area_zip_prefixes=["750", "751", "752"],
            performance_score=0.72,
        )
        agents["peach"] = self._make_agent(
            "agent.peach", "Peach Atlantez",
            licensed_states=["GA"], service_area_zip_prefixes=["303", "300"],
            performance_score=0.83,
        )
        agents["brook"] = self._make_agent(
            "agent.brook", "Brook Buckhead",
            licensed_states=["GA"], service_area_zip_prefixes=["303", "300"],
            performance_score=None, active_referral_count=0,
        )
        agents["coral"] = self._make_agent(
            "agent.coral", "Coral Keysworth",
            licensed_states=["FL"], service_area_zip_prefixes=["331", "328"],
            performance_score=0.79,
        )
        agents["reed"] = self._make_agent(
            "agent.reed", "Reed Orlandale",
            licensed_states=["FL"], service_area_zip_prefixes=["331", "328"],
            performance_score=0.61,
        )
        agents["sage"] = self._make_agent(
            "agent.sage", "Sage Scottsdale",
            licensed_states=["AZ"], service_area_zip_prefixes=["850", "852"],
            performance_score=0.87, capacity=4,
        )
        agents["quill"] = self._make_agent(
            "agent.quill", "Quill Tempeston",
            licensed_states=["AZ"], service_area_zip_prefixes=["850", "852"],
            performance_score=0.58,
        )
        return agents

    def _new_referral(self, rng, now, *, state=None, minutes_ago=0, **kwargs):
        state = state or rng.choice(list(MARKETS))
        market = MARKETS[state]
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        n = Referral.objects.count() + 1
        defaults = dict(
            external_id=f"lead-{n:05d}",
            customer_name=f"{first} {last}",
            customer_email=f"{first.lower()}.{last.lower()}{n}@example.com",
            customer_phone=f"555-01{rng.randint(10, 99)}",
            state=state,
            postal_code=f"{rng.choice(market['zips'])}{rng.randint(10, 99):02d}",
            estimated_price=(
                rng.randrange(250_000, 900_000, 25_000)
                if rng.random() < 0.8
                else rng.randrange(900_000, 1_350_000, 25_000)
            ),
            intent_level=rng.choice(list(Referral.Intent)),
        )
        defaults.update(kwargs)
        referral = Referral.objects.create(**defaults)
        if minutes_ago:
            created = now - timedelta(minutes=minutes_ago)
            Referral.objects.filter(pk=referral.pk).update(created_at=created)
            referral.refresh_from_db()
        record_event(
            referral,
            ReferralEvent.Type.REFERRAL_RECEIVED,
            actor="intake-webhook",
            payload={"event_id": f"evt-{referral.external_id}"},
        )
        return referral

    def _offer_and_backdate(self, referral, minutes_ago, now):
        offers = matching.build_offers(referral)
        if minutes_ago:
            offered = now - timedelta(minutes=minutes_ago)
            for offer in offers:
                Offer.objects.filter(pk=offer.pk).update(
                    offered_at=offered,
                    expires_at=offered + timedelta(minutes=matching.OFFER_TTL_MINUTES),
                )
        return list(Offer.objects.filter(referral=referral).order_by("-score"))

    def _seed_pilot_referrals(self, rng, agents, now):
        self.stdout.write("Seeding pilot referrals...")

        # Fresh Denver referrals with live offers (the working queue).
        for minutes_ago in (2, 4, 6):
            referral = self._new_referral(
                rng, now, state="CO", minutes_ago=minutes_ago,
                estimated_price=rng.randrange(300_000, 850_000, 25_000),
                intent_level=Referral.Intent.HOT,
            )
            self._offer_and_backdate(referral, minutes_ago - 1, now)

        # A live referral whose phone the notification gateway will reject.
        bad_phone = self._new_referral(
            rng, now, state="CO", minutes_ago=3,
            customer_phone="555-0157 ext. TBD",
            estimated_price=525_000,
            intent_level=Referral.Intent.HOT,
        )
        self._offer_and_backdate(bad_phone, 2, now)

        # Duplicate intake: the same customer request ingested twice.
        dup_kwargs = dict(
            state="CO",
            external_id="lead-77401",
            customer_name="Rowan Mockwell",
            customer_email="rowan.mockwell@example.com",
            customer_phone="555-0114",
            postal_code="80231",
            estimated_price=610_000,
            intent_level=Referral.Intent.WARM,
        )
        dup_one = self._new_referral(rng, now, minutes_ago=95, **dup_kwargs)
        self._offer_and_backdate(dup_one, 94, now)
        record_event(
            dup_one,
            ReferralEvent.Type.REFERRAL_RECEIVED,
            actor="intake-webhook",
            payload={"event_id": "seed-duplicate-event", "duplicate_ignored": True},
        )

        # Referrals stalled after every offer was declined or expired.
        for minutes_ago in (240, 180, 130):
            referral = self._new_referral(rng, now, state="CO", minutes_ago=minutes_ago)
            offers = self._offer_and_backdate(referral, minutes_ago - 1, now)
            for i, offer in enumerate(offers):
                if i == 0:
                    offer.status = Offer.Status.DECLINED
                    offer.responded_at = now - timedelta(minutes=minutes_ago - 8)
                    offer.save(update_fields=["status", "responded_at"])
                    record_event(referral, ReferralEvent.Type.OFFER_DECLINED,
                                 actor=offer.agent.name, payload={"offer_id": offer.id})
                else:
                    Offer.objects.filter(pk=offer.pk).update(status=Offer.Status.EXPIRED)
                    record_event(referral, ReferralEvent.Type.OFFER_EXPIRED,
                                 actor="system", payload={"offer_id": offer.id})

        # A cleanly assigned referral.
        assigned = self._new_referral(rng, now, state="CO", minutes_ago=60)
        offers = self._offer_and_backdate(assigned, 59, now)
        winner = offers[0]
        winner.status = Offer.Status.ACCEPTED
        winner.responded_at = now - timedelta(minutes=55)
        winner.save(update_fields=["status", "responded_at"])
        Claim.objects.create(referral=assigned, agent=winner.agent)
        assigned.status = Referral.Status.ASSIGNED
        assigned.assigned_agent = winner.agent
        assigned.save(update_fields=["status", "assigned_agent"])
        record_event(assigned, ReferralEvent.Type.CLAIM_SUCCEEDED,
                     actor=winner.agent.name, payload={"offer_id": winner.id})

        # The double-claim incident Operations reported: only one active claim is valid;
        # the conflicting claim is retained as historical state for visibility.
        conflicted = self._new_referral(rng, now, state="CO", minutes_ago=45)
        offers = self._offer_and_backdate(conflicted, 44, now)
        if len(offers) >= 2:
            first, second = offers[0], offers[1]
            for offer, mins, active in ((first, 40, True), (second, 40, False)):
                offer.status = Offer.Status.ACCEPTED
                offer.responded_at = now - timedelta(minutes=mins)
                offer.save(update_fields=["status", "responded_at"])
                Claim.objects.create(
                    referral=conflicted,
                    agent=offer.agent,
                    active=active,
                    released_at=(now - timedelta(minutes=mins - 3)) if not active else None,
                )
                record_event(conflicted, ReferralEvent.Type.CLAIM_SUCCEEDED,
                             actor=offer.agent.name, payload={"offer_id": offer.id})
            conflicted.status = Referral.Status.ASSIGNED
            conflicted.assigned_agent = first.agent
            conflicted.save(update_fields=["status", "assigned_agent"])

        # Out-of-market referrals so other agents also have queues.
        for state in ("TX", "GA", "FL"):
            referral = self._new_referral(rng, now, state=state, minutes_ago=8)
            self._offer_and_backdate(referral, 7, now)

    def _seed_history(self, rng, agents, now, count):
        self.stdout.write(f"Seeding {count} historical referrals...")
        agent_list = list(agents.values())
        for _ in range(count):
            minutes_ago = rng.randint(2 * 24 * 60, 60 * 24 * 60)
            referral = self._new_referral(rng, now, minutes_ago=minutes_ago)
            offers = self._offer_and_backdate(referral, minutes_ago - 2, now)
            outcome = rng.random()
            if outcome < 0.6 and offers:
                winner = rng.choice(offers)
                Offer.objects.filter(pk=winner.pk).update(
                    status=Offer.Status.ACCEPTED,
                    responded_at=now - timedelta(minutes=minutes_ago - 6),
                )
                Claim.objects.create(referral=referral, agent=winner.agent, active=False,
                                     released_at=now - timedelta(minutes=minutes_ago - 500))
                referral.status = Referral.Status.CLOSED
                referral.assigned_agent = winner.agent
                referral.save(update_fields=["status", "assigned_agent"])
                record_event(referral, ReferralEvent.Type.CLAIM_SUCCEEDED,
                             actor=winner.agent.name, payload={"offer_id": winner.id})
            elif offers:
                Offer.objects.filter(referral=referral).update(status=Offer.Status.EXPIRED)
                for offer in offers:
                    record_event(referral, ReferralEvent.Type.OFFER_EXPIRED,
                                 actor="system", payload={"offer_id": offer.id})
                referral.status = Referral.Status.CLOSED
                referral.save(update_fields=["status"])
        assert agent_list
