from django.core.management.base import BaseCommand
from django.utils import timezone

from referrals.matching import process_expired_offers
from referrals.models import Offer


class Command(BaseCommand):
    help = "Expire overdue offers and advance matching for affected referrals."

    def handle(self, *args, **options):
        advanced = process_expired_offers()
        overdue = Offer.objects.filter(
            status=Offer.Status.PENDING,
            expires_at__lte=timezone.now(),
        ).count()
        summary = f"Processed {advanced} referrals; overdue offers remaining: {overdue}"
        self.stdout.write(self.style.SUCCESS(summary))
