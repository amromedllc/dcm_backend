"""Server-enforced idle sessions. Polling and token refresh never count as activity."""
import hashlib
import math

import jwt
from django.utils import timezone
from ninja.errors import HttpError
from redis.exceptions import RedisError

# Validation and touching share a single operation so a late heartbeat cannot
# resurrect a timed-out session. Keep state through the absolute token deadline.
_ACTIVITY = """
local last = tonumber(redis.call('GET', KEYS[1])) or tonumber(ARGV[1])
if tonumber(ARGV[2]) - last >= tonumber(ARGV[3]) then return 0 end
if ARGV[4] == '1' then
  redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[5])
elseif redis.call('EXISTS', KEYS[1]) == 0 then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[5], 'NX')
end
return 1
"""


def enforce_session_activity(payload, tenant, *, touch=False):
    if not tenant or not getattr(tenant, 'automatic_logout_enabled', False):
        return
    from django.conf import settings
    from .auth import _redis, session_start_from_payload
    start = session_start_from_payload(payload)
    sid, baseline = payload.get('sid'), payload.get('idle_started_at')
    if not isinstance(sid, str) or not sid or isinstance(baseline, bool) or not isinstance(baseline, (int, float)) or not math.isfinite(baseline):
        raise jwt.InvalidTokenError('Please sign in again to initialize a secure session')
    now = timezone.now().timestamp()
    if baseline < start or baseline > now:
        raise jwt.InvalidTokenError('Invalid idle-session timestamp')
    timeout = max(1, int(tenant.automatic_logout_minutes)) * 60
    identity = f'{payload["org_id"]}:{payload["sub"]}:{sid}'
    key = 'dcm:session:activity:' + hashlib.sha256(identity.encode()).hexdigest()
    ttl = max(1, math.ceil(start + settings.AUTH_SESSION_MAX_HOURS * 3600 - now))
    try:
        active = _redis().eval(_ACTIVITY, 1, key, baseline, now, timeout, int(touch), ttl)
    except RedisError as exc:
        raise HttpError(503, 'Session security is temporarily unavailable. Please retry.') from exc
    if not active:
        raise HttpError(423, 'Session locked due to inactivity. Unlock with your PIN or sign in again.')
