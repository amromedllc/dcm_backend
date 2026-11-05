from django.db import migrations
from django.utils import timezone


def observe_existing_clients(apps, schema_editor):
    Client = apps.get_model('clients', 'Client')
    Observation = apps.get_model('clients', 'ClientStatusObservation')
    now = timezone.now()
    batch = []
    for client in Client.objects.exclude(organization_id=None).iterator(chunk_size=500):
        batch.append(Observation(client_id=client.pk, organization_id=client.organization_id,
                                 status=client.status, observed_at=now))
        if len(batch) == 500:
            Observation.objects.bulk_create(batch, batch_size=500)
            batch = []
    if batch:
        Observation.objects.bulk_create(batch, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('clients', '0012_clientstatusobservation')]
    operations = [migrations.RunPython(observe_existing_clients, migrations.RunPython.noop)]
