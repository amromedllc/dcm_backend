from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Client, ClientStatusObservation


@transaction.atomic
def record_client_statuses(client_ids):
    # Re-read committed status while holding the owning row, including bulk syncs.
    latest = ClientStatusObservation.objects.filter(client_id=OuterRef('pk')).order_by('-observed_at', '-id').values('status')[:1]
    rows = Client.objects.select_for_update().filter(pk__in=client_ids).annotate(last_observed_status=Subquery(latest)).order_by('pk')
    observations = [ClientStatusObservation(client_id=client.pk, organization_id=client.organization_id, status=client.status)
                    for client in rows if client.last_observed_status != client.status]
    ClientStatusObservation.objects.bulk_create(observations, batch_size=500)


@receiver(post_save, sender=Client)
def record_saved_client_status(sender, instance, raw=False, **kwargs):
    if not raw:
        record_client_statuses([instance.pk])
