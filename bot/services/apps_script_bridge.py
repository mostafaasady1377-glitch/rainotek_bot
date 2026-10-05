"""Signed requests to the account-owned RAINOTEK Apps Script bridge."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlparse
import aiohttp


class SheetBridgeError(RuntimeError):
    pass


class AppsScriptBridge:
    def __init__(self, url, secret, proxy=None):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'script.google.com' or not parsed.path.startswith('/macros/s/') or not parsed.path.endswith('/exec') or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Invalid Apps Script deployment URL')
        if not secret:
            raise ValueError('Bridge secret is missing')
        self.url, self.secret, self.proxy = url, secret, proxy

    def envelope(self, action, **kwargs):
        payload = json.dumps(dict(kwargs, action=action, nonce=uuid.uuid4().hex, timestamp=int(time.time()*1000)), ensure_ascii=False, separators=(',', ':'))
        signature = base64.urlsafe_b64encode(hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest()).decode()
        return dict(payload=payload, signature=signature)

    async def request(self, action, **kwargs):
        connector = None
        if self.proxy:
            from aiohttp_socks import ProxyConnector
            connector = ProxyConnector.from_url(self.proxy)
        try:
            async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=40)) as client:
                async with client.post(self.url, json=self.envelope(action, **kwargs)) as response:
                    if response.status != 200:
                        raise SheetBridgeError('Google connection failed')
                    result = await response.json(content_type=None)
                    if not isinstance(result, dict) or result.get('ok') is not True:
                        error = result.get('error') if isinstance(result, dict) else None
                        raise SheetBridgeError('Sheet changed; reload product' if error == 'conflict' else 'Sheet request failed')
                    return result
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            # A timed-out write may have succeeded: refresh before retry, never blindly resend.
            raise SheetBridgeError('Sheet response unavailable; refresh before retrying') from exc

    async def read(self, gid=0):
        result = await self.request('read', gid=gid)
        if not isinstance(result.get('rows'), list):
            raise SheetBridgeError('Invalid sheet response')
        return result['rows']

    async def update(self, row, expected_row, changes, gid=0):
        return await self.request('update', gid=gid, row=row, expectedRow=expected_row, changes=changes)

    async def update_many(self, edits, gid=0):
        return await self.request('update_many', gid=gid, edits=edits)


def configured_bridge():
    from bot.config import get_settings
    settings = get_settings()
    if not settings.GOOGLE_SHEET_BRIDGE_URL or not settings.GOOGLE_SHEET_BRIDGE_SECRET:
        raise SheetBridgeError('اتصال نوشتن گوگل‌شیت هنوز فعال نشده است؛ هیچ تغییری ثبت نشد.')
    return AppsScriptBridge(settings.GOOGLE_SHEET_BRIDGE_URL, settings.GOOGLE_SHEET_BRIDGE_SECRET, settings.NETWORK_PROXY_URL)
