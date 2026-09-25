"""Minimal client for any OpenAI-compatible chat-completions API (NVIDIA, Ollama, Gemini's
compatibility endpoint, OpenAI...). Configured with AI_API_BASE_URL / AI_API_KEY / AI_MODEL.

Callers must never put client (patient) information in a prompt: development providers are
free cloud services and their data handling isn't covered by our agreements."""
from __future__ import annotations

import json
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class AIError(Exception):
    """An AI call failed. `status` is the HTTP status to show the user."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def is_configured() -> bool:
    return bool(getattr(settings, 'AI_API_KEY', ''))


def is_enabled() -> bool:
    """AI features are on only when the AI_ENABLED switch is true and a key is configured."""
    return bool(getattr(settings, 'AI_ENABLED', False)) and is_configured()


def extract_json(text: str) -> dict:
    """Models often wrap JSON in prose or code fences; pull out the first object."""
    cleaned = re.sub(r'```(?:json)?', '', text or '').strip()
    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start == -1 or end <= start:
        raise AIError('The AI response could not be understood. Please try again.')
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AIError('The AI response could not be understood. Please try again.') from exc
    if not isinstance(parsed, dict):
        raise AIError('The AI response could not be understood. Please try again.')
    return parsed


def chat_json(system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.4) -> dict:
    """Send one system+user prompt and return the reply parsed as a JSON object."""
    if not getattr(settings, 'AI_ENABLED', False):
        raise AIError('AI features are turned off.', status=403)
    if not is_configured():
        raise AIError('AI drafting is not set up. Ask an administrator to add an AI key.', status=503)
    url = settings.AI_API_BASE_URL.rstrip('/') + '/chat/completions'
    try:
        response = requests.post(
            url,
            headers={'Authorization': f'Bearer {settings.AI_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': settings.AI_MODEL,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                'temperature': temperature,
                'max_tokens': max_tokens,
            },
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning('AI request failed before a response (%s): %s', url, exc)
        raise AIError('The AI service could not be reached. Please try again.') from exc
    if not response.ok:
        # Log what the provider said (never the key) so setup problems are diagnosable.
        logger.warning(
            'AI provider returned HTTP %s for model %s at %s: %s',
            response.status_code, settings.AI_MODEL, url, (getattr(response, 'text', '') or '')[:500],
        )
    if response.status_code in (401, 403):
        raise AIError('The AI service rejected the configured key.', status=502)
    if response.status_code in (404, 410):
        raise AIError(
            f'The AI model "{settings.AI_MODEL}" is not available (not found or retired). '
            'Set AI_MODEL to a model your provider currently offers.',
            status=502,
        )
    if response.status_code == 429:
        raise AIError('The AI service is busy. Please wait a moment and try again.', status=429)
    if not response.ok:
        raise AIError('The AI service returned an error. Please try again.')
    try:
        content = response.json()['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning('AI reply had an unexpected shape: %s', (getattr(response, 'text', '') or '')[:500])
        raise AIError('The AI response could not be understood. Please try again.') from exc
    return extract_json(content)
