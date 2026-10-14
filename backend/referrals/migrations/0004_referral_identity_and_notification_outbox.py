from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Count
from django.utils import timezone


def merge_duplicate_referrals(apps, schema_editor):
    Referral = apps.get_model("referrals", "Referral")
    Offer = apps.get_model("referrals", "Offer")
    Claim = apps.get_model("referrals", "Claim")
    ReferralEvent = apps.get_model("referrals", "ReferralEvent")
    Agent = apps.get_model("referrals", "Agent")
    database = schema_editor.connection.alias

    duplicate_ids = list(
        Referral.objects.using(database)
        .values("external_id")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .values_list("external_id", flat=True)
    )
    for external_id in duplicate_ids:
        referrals = list(
            Referral.objects.using(database)
            .filter(external_id=external_id)
            .order_by("created_at", "pk")
        )
        canonical, *duplicates = referrals
        referral_ids = [referral.pk for referral in referrals]
        claims = list(
            Claim.objects.using(database)
            .filter(referral_id__in=referral_ids)
            .order_by("created_at", "pk")
        )
        active_claims = [claim for claim in claims if claim.active]
        winner = next(
            (
                claim
                for claim in active_claims
                if claim.agent_id == canonical.assigned_agent_id
            ),
            active_claims[0] if active_claims else None,
        )

        Claim.objects.using(database).filter(
            pk__in=[claim.pk for claim in active_claims]
        ).update(active=False, released_at=timezone.now())

        duplicate_referral_ids = [referral.pk for referral in duplicates]
        for related_model in (Offer, ReferralEvent):
            related_model.objects.using(database).filter(
                referral_id__in=duplicate_referral_ids
            ).update(referral_id=canonical.pk)
        Claim.objects.using(database).filter(
            referral_id__in=duplicate_referral_ids
        ).update(referral_id=canonical.pk)

        if winner is not None:
            Claim.objects.using(database).filter(pk=winner.pk).update(
                active=True, released_at=None
            )
            canonical.assigned_agent_id = winner.agent_id
            canonical.status = "assigned"
        else:
            assigned_duplicate = next(
                (
                    referral
                    for referral in reversed(duplicates)
                    if referral.status == "assigned" and referral.assigned_agent_id
                ),
                None,
            )
            if canonical.status != "assigned" and assigned_duplicate is not None:
                canonical.assigned_agent_id = assigned_duplicate.assigned_agent_id
                canonical.status = "assigned"
        canonical.save(update_fields=["assigned_agent", "status"])

        if canonical.status == "assigned":
            Offer.objects.using(database).filter(
                referral_id=canonical.pk, status="pending"
            ).update(status="declined", responded_at=timezone.now())
        Referral.objects.using(database).filter(
            pk__in=duplicate_referral_ids
        ).delete()

    for agent in Agent.objects.using(database).all().iterator():
        active_count = Claim.objects.using(database).filter(
            agent_id=agent.pk, active=True
        ).count()
        if agent.active_referral_count != active_count:
            Agent.objects.using(database).filter(pk=agent.pk).update(
                active_referral_count=active_count
            )


class Migration(migrations.Migration):
    dependencies = [
        ("referrals", "0003_processedwebhookevent"),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_referrals, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="referral",
            name="external_id",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.CreateModel(
            name="NotificationOutbox",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("sent", "Sent")],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=timezone.now)),
                ("last_error_code", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "claim",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_outbox",
                        to="referrals.claim",
                    ),
                ),
            ],
        ),
    ]
