"""Thin HTTP client for Wiley's PracticePlanners API.

Deliberately dumb: no retries, no caching here (retention lives in
snapshots.py once content reaches the provider layer). Never logs the API
key, request/response bodies, or raw clinical text.
"""
import time
from typing import Any

import requests
from django.conf import settings


class WileyApiError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


DEFAULT_TIMEOUT_SECONDS = 15
# Confirmed with Wiley 2026-11-18 (QA key, 30-day trial) — see project memory
# project_wiley_api_trial.md. Re-check before any production cutover.
RATE_LIMITS = {'search': 10, 'retrieval': 30}


def _redis():
    import redis
    return redis.from_url(settings.REDIS_URL)


def _enforce_rate_limit(bucket: str) -> None:
    """Best-effort per-second counter shared across worker processes via Redis.
    Fixed 1-second windows, not a true sliding window — adequate for staying
    well clear of Wiley's documented limits, not for squeezing up to them."""
    limit = RATE_LIMITS[bucket]
    key = f'wiley:ratelimit:{bucket}:{int(time.time())}'
    try:
        client = _redis()
        count = client.incr(key)
        if count == 1:
            client.expire(key, 2)
    except Exception:
        return  # Redis unavailable: fail open rather than block clinical content lookups.
    if count > limit:
        raise WileyApiError(f'Wiley API rate limit ({limit}/s for {bucket}) reached; try again shortly', status_code=429)


def _headers() -> dict[str, str]:
    api_key = getattr(settings, 'WILEY_API_KEY', '').strip()
    if not api_key:
        raise WileyApiError('Wiley API key is not configured')
    import hashlib
    fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:8]
    print(f'[wiley debug] key present, length={len(api_key)}, fingerprint={fingerprint}')
    return {'X-API-Key': api_key}


def fetch_json(path: str, *, params: dict[str, Any] | None = None, bucket: str = 'retrieval') -> Any:
    _enforce_rate_limit(bucket)
    base = getattr(settings, 'WILEY_API_BASE_URL', '').rstrip('/')
    try:
        response = requests.get(f'{base}{path}', params=params, headers=_headers(), timeout=DEFAULT_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise WileyApiError('Wiley API request failed') from exc
    if response.status_code in (401, 403):
        raise WileyApiError('Wiley API key missing or invalid', status_code=403)
    if response.status_code == 404:
        raise WileyApiError('Not found', status_code=404)
    if not response.ok:
        raise WileyApiError(f'Wiley API error {response.status_code}', status_code=response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise WileyApiError('Wiley API returned an unparseable response') from exc


def fetch_pdf(path: str) -> bytes:
    _enforce_rate_limit('retrieval')
    base = getattr(settings, 'WILEY_API_BASE_URL', '').rstrip('/')
    try:
        response = requests.get(f'{base}{path}', headers=_headers(), timeout=DEFAULT_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise WileyApiError('Wiley API request failed') from exc
    if response.status_code in (401, 403):
        raise WileyApiError('Wiley API key missing or invalid', status_code=403)
    if response.status_code == 404:
        raise WileyApiError('Not found', status_code=404)
    if not response.ok:
        raise WileyApiError(f'Wiley API error {response.status_code}', status_code=response.status_code)
    return response.content
