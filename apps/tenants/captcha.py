import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_TURNSTILE_VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def verify_captcha(token: str, remote_ip: str = '') -> bool:
    """Verify a Cloudflare Turnstile token server-side.

    Returns False (never raises) on any provider/network failure — a signup
    request fails closed rather than silently skipping verification.
    """
    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    if not secret:
        logger.error('TURNSTILE_SECRET_KEY is not configured — rejecting signup captcha check')
        return False
    if not token:
        return False
    try:
        resp = requests.post(
            _TURNSTILE_VERIFY_URL,
            data={'secret': secret, 'response': token, 'remoteip': remote_ip},
            timeout=10,
        )
        resp.raise_for_status()
        return bool(resp.json().get('success'))
    except (requests.RequestException, ValueError):
        logger.exception('Turnstile captcha verification request failed')
        return False
