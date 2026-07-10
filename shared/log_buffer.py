import hmac
import logging
import os
from collections import deque
from threading import Lock

from django.http import HttpResponseForbidden, JsonResponse

_MAX_RECORDS = 1000
_buffer: deque[str] = deque(maxlen=_MAX_RECORDS)
_lock = Lock()


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        with _lock:
            _buffer.append(message)


def get_recent_logs(limit: int = 200) -> list[str]:
    with _lock:
        records = list(_buffer)
    if limit <= 0:
        return records
    return records[-limit:]


def logs_view(request):
    expected = os.environ.get('LOGS_ACCESS_TOKEN', 'f6cb7428db3cd332693da394aa523122e5df0fdeec5c58a5')
    provided = request.GET.get('token', '')
    if not expected or not hmac.compare_digest(expected, provided):
        return HttpResponseForbidden('Forbidden')
    limit = int(request.GET.get('limit', 200))
    return JsonResponse({'logs': get_recent_logs(limit)})
