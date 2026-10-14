from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("referrals", "0002_claim_active_unique"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProcessedWebhookEvent",
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
                ("event_id", models.CharField(max_length=128, unique=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
