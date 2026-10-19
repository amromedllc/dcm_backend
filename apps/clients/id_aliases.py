"""Resolve the local client key and numeric external patient ID."""


def client_identifier_aliases(client_id: int) -> set[int]:
    """Return every integer ID used to associate data with this client."""
    from .models import Client

    ids = {int(client_id)}
    client = Client.objects.filter(id=client_id).only('id', 'external_id').first()
    if client is None:
        client = Client.objects.filter(external_id=str(client_id)).only('id', 'external_id').first()
    if client is None:
        return ids

    ids.add(client.id)
    if client.external_id and client.external_id.isdigit():
        ids.add(int(client.external_id))
    return ids
