from apps.clients.api import _map_patient_fields
from apps.clients.models import Client


def test_tpms_in_active_status_maps_to_inactive():
    fields = _map_patient_fields(
        {
            'patient_id': 123,
            'client_first_name': 'Jane',
            'client_last_name': 'Doe',
            'patient_active_status': 'In-Active',
        },
        fallback_admin_id=1333,
    )

    assert fields is not None
    assert fields['status'] == Client.Status.INACTIVE
    assert fields['is_active'] is False
