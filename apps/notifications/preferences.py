PREFERENCE_TYPES = [
    ('file_upload_failed', 'File upload failed'),
    ('session_attachment_added', 'Files, photo or video attached to a session'),
    ('video_session_invitation', 'Invitation to a video session'),
    ('missed_appointment', 'Missed Appointment Notification'),
    ('new_message', 'New message received'),
    ('program_modified', 'New program modification'),
    ('target_mastered', 'New target mastered'),
    ('parent_session_reminder', 'Parent session reminder'),
    ('report_review_request', 'Report review request'),
    ('session_finished', 'Session finished'),
    ('session_not_finished_24h', 'Session not finished (remains open after 24 hours)'),
    ('session_note_not_rendered', 'Session note not yet rendered'),
    ('session_note_pending_completion', 'Session note pending completion'),
    ('signature_request', 'Signature request'),
    ('target_reopened', 'Target is reopened'),
]

EVENT_TYPE_TO_PREFERENCE_TYPE = {
    'session_submitted': 'report_review_request',
    'session_approved': 'session_finished',
    'session_rejected': 'session_note_pending_completion',
}


def preference_key_for_event(event_type: str) -> str:
    return EVENT_TYPE_TO_PREFERENCE_TYPE.get(event_type, event_type)
