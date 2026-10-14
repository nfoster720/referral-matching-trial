from datetime import timedelta
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from referrals import notifications
from referrals.models import NotificationOutbox

MAX_ATTEMPTS = 8
LEASE_MINUTES = 5


class Command(BaseCommand):
    help = "Deliver pending claim notifications and schedule retries for failures."

    def handle(self, *args, **options):
        now = timezone.now()
        outbox_ids = list(
            NotificationOutbox.objects.filter(
                Q(
                    status=NotificationOutbox.Status.PENDING,
                    next_attempt_at__lte=now,
                )
                | Q(
                    status=NotificationOutbox.Status.PROCESSING,
                    lease_expires_at__lte=now,
                )
            )
            .order_by("created_at")
            .values_list("id", flat=True)
        )
        sent = 0
        retrying = 0
        terminal = 0

        for outbox_id in outbox_ids:
            with transaction.atomic():
                item = (
                    NotificationOutbox.objects.select_for_update()
                    .select_related("claim__referral", "claim__agent")
                    .filter(pk=outbox_id)
                    .first()
                )
                if item is None:
                    continue
                claim_time = timezone.now()
                is_due = (
                    item.status == NotificationOutbox.Status.PENDING
                    and item.next_attempt_at <= claim_time
                ) or (
                    item.status == NotificationOutbox.Status.PROCESSING
                    and item.lease_expires_at is not None
                    and item.lease_expires_at <= claim_time
                )
                if not is_due:
                    continue
                if item.attempts >= MAX_ATTEMPTS:
                    item.status = NotificationOutbox.Status.FAILED
                    item.lease_expires_at = None
                    item.lease_token = None
                    item.last_error_code = item.last_error_code or "retry_limit_reached"
                    item.save(
                        update_fields=[
                            "status",
                            "lease_expires_at",
                            "lease_token",
                            "last_error_code",
                        ]
                    )
                    terminal += 1
                    continue

                item.attempts += 1
                lease_token = uuid4()
                item.status = NotificationOutbox.Status.PROCESSING
                item.lease_token = lease_token
                item.lease_expires_at = claim_time + timedelta(minutes=LEASE_MINUTES)
                item.save(
                    update_fields=[
                        "attempts",
                        "status",
                        "lease_token",
                        "lease_expires_at",
                    ]
                )
                claim = item.claim
                idempotency_key = f"claim-confirmation-{claim.pk}"

            try:
                notifications.send_claim_confirmation(
                    claim,
                    idempotency_key=idempotency_key,
                )
            except notifications.NotificationError:
                delivery_error = "delivery_failed"
            else:
                delivery_error = None

            with transaction.atomic():
                item = NotificationOutbox.objects.select_for_update().get(pk=outbox_id)
                if item.status != NotificationOutbox.Status.PROCESSING:
                    continue
                if item.lease_token != lease_token:
                    continue

                item.lease_expires_at = None
                item.lease_token = None
                if delivery_error is None:
                    item.status = NotificationOutbox.Status.SENT
                    item.sent_at = timezone.now()
                    item.last_error_code = ""
                    sent += 1
                elif item.attempts >= MAX_ATTEMPTS:
                    item.status = NotificationOutbox.Status.FAILED
                    item.last_error_code = delivery_error
                    terminal += 1
                else:
                    item.status = NotificationOutbox.Status.PENDING
                    item.last_error_code = delivery_error
                    item.next_attempt_at = timezone.now() + timedelta(
                        minutes=min(2**min(item.attempts, 8), 60)
                    )
                    retrying += 1
                item.save(
                    update_fields=[
                        "status",
                        "sent_at",
                        "last_error_code",
                        "next_attempt_at",
                        "lease_expires_at",
                        "lease_token",
                    ]
                )

        now = timezone.now()
        due_remaining = NotificationOutbox.objects.filter(
            Q(
                status=NotificationOutbox.Status.PENDING,
                next_attempt_at__lte=now,
            )
            | Q(
                status=NotificationOutbox.Status.PROCESSING,
                lease_expires_at__lte=now,
            )
        ).count()
        summary = (
            f"Notifications sent: {sent}; retrying: {retrying}; "
            f"terminal failures: {terminal}; due remaining: {due_remaining}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
