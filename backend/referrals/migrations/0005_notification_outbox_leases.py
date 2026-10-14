from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("referrals", "0004_referral_identity_and_notification_outbox"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationoutbox",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notificationoutbox",
            name="lease_token",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="notificationoutbox",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("processing", "Processing"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
    ]
