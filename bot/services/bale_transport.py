"""Bale compatibility boundary; business handlers and inventory remain shared."""
import json
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import PurePosixPath
from loguru import logger

from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import Default
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import BufferedInputFile, InputFile

from bot.services.platform_identity import (
    BALE_FILE_PREFIX, bale_identity, is_bale_identity, native_identity,
)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []

    def handle_data(self, data):
        if self.links and self.links[-1].startswith('https://ble.ir/'):
            data = data.replace('تلگرام', 'بله')
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))

    def handle_endtag(self, tag):
        if tag == 'a' and self.links:
            url = self.links.pop()
            if url.startswith('tg://user?id='):
                try:
                    if is_bale_identity(int(url.split('=')[-1])):
                        return
                except ValueError:
                    return
            if url:
                self.parts.append(' (' + url + ')')


def bale_text(value, *, html=True, entities=None):
    if html:
        parser = PlainText()
        parser.feed(value)
        text = ''.join(parser.parts)
    else:
        text = value
        # Telegram text_link entities use UTF-16 offsets; append URLs without
        # interpreting already-plain model names such as '<model>' as HTML.
        for entity in entities or []:
            if entity.get('type') == 'text_link':
                url = entity.get('url') or ''
                if url.startswith(('https://', 'http://')) and url not in text:
                    text += '\n' + url
    text = text.replace('دکمهٔ رسمی تلگرام', 'دکمهٔ رسمی بله').replace('از طریق تلگرام', 'از طریق بله')
    text = re.sub(r'https://t\.me/(Rinotek_bot)(\?start=[A-Za-z0-9_-]+)?', r'https://ble.ir/\1\2', text, flags=re.I)
    text = re.sub(r'(?<!\d)-\d{17,}(?!\d)', lambda m: 'بله: ' + str(native_identity(int(m[0]))) if is_bale_identity(int(m[0])) else m[0], text)
    return text


def safe_keyboard(markup, platform):
    if not isinstance(markup, dict) or 'inline_keyboard' not in markup:
        return markup
    rows = []
    for row in markup['inline_keyboard']:
        buttons = []
        for button in row:
            button = dict(button)
            url = button.get('url') or ''
            if url.startswith('tg://user?id='):
                try:
                    if is_bale_identity(int(url.split('=')[-1])):
                        continue  # Bale does not document anonymous account chat links.
                except ValueError:
                    continue
            if platform == 'bale' and url:
                button['url'] = re.sub(r'https://t\.me/Rinotek_bot', 'https://ble.ir/Rinotek_bot', url, flags=re.I)
            if (button.get('url') or '').startswith('https://ble.ir/'):
                button['text'] = button['text'].replace('تلگرام', 'بله')
            buttons.append(button)
        if buttons:
            rows.append(buttons)
    return {**markup, 'inline_keyboard': rows}


class BoundedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > 30 * 1024 * 1024:
            raise ValueError('فایل برای انتقال بین پیام‌رسان‌ها بیش از ۳۰ مگابایت است.')
        return super().write(data)


def normalize_bale_payload(value, parent=''):
    if isinstance(value, list):
        return [normalize_bale_payload(item, parent) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    if parent == 'callback_query' and 'chat_instance' not in value:
        # Bale omits Telegram-only chat_instance; it is metadata, never authorization.
        chat_id = (value.get('message') or {}).get('chat', {}).get('id', 'inline')
        result['chat_instance'] = 'bale:' + str(chat_id)
    if parent == 'message' and 'message_id' in value and 'date' not in value:
        result['date'] = 0  # Telegram's inaccessible-message sentinel for old callbacks.
    for key, item in value.items():
        if key == 'id' and parent in {'from', 'chat', 'user', 'sender_chat'} and isinstance(item, int):
            result[key] = bale_identity(item)
        elif key == 'user_id' and isinstance(item, int):
            result[key] = bale_identity(item)
        elif key == 'file_id' and isinstance(item, str):
            result[key] = item if item.startswith(BALE_FILE_PREFIX) else BALE_FILE_PREFIX + item
        else:
            result[key] = normalize_bale_payload(item, key)
    return result


class CrossFileServer:
    """Route download URLs by provenance, never reuse the other bot's token."""
    is_local = False

    def __init__(self, platform, own_token, peer_token):
        self.platform = platform
        self.own_token = own_token
        self.peer_token = peer_token
        self.server = TelegramAPIServer.from_base('https://tapi.bale.ai' if platform == 'bale' else 'https://api.telegram.org')

    def api_url(self, token, method):
        return self.server.api_url(token, method)

    def file_url(self, token, path):
        if path.startswith('peerfile:'):
            peer = TelegramAPIServer.from_base('https://api.telegram.org' if self.platform == 'bale' else 'https://tapi.bale.ai')
            return peer.file_url(self.peer_token, path[len('peerfile:'):])
        return self.server.file_url(token, path)


class PlatformSession(AiohttpSession):
    def __init__(self, platform, own_token, peer_token, **kwargs):
        super().__init__(**kwargs)
        self.platform = platform
        self.api = CrossFileServer(platform, own_token, peer_token)
        self.peer_bot = None
        self.owner_bot = None

    def check_response(self, bot, method, status_code, content):
        if self.platform == 'bale':
            payload = json.loads(content)
            if payload.get('ok') and method.__api_method__ != 'getMe':
                payload['result'] = normalize_bale_payload(payload.get('result'))
                if method.__api_method__ == 'getChat' and isinstance(payload['result'], dict) and 'id' in payload['result']:
                    payload['result']['id'] = bale_identity(payload['result']['id'])
            content = json.dumps(payload, ensure_ascii=False)
        return super().check_response(bot, method, status_code, content)

    async def _file_value(self, value, field):
        if isinstance(value, InputFile) or not isinstance(value, str):
            return value
        if value.startswith(('http://', 'https://', 'attach://')):
            return value
        source = 'bale' if value.startswith(BALE_FILE_PREFIX) else 'telegram'
        if source == self.platform:
            return value.removeprefix(BALE_FILE_PREFIX)
        # Cross-platform attachments must be downloaded and uploaded, not forwarded IDs.
        metadata = await self.peer_bot.get_file(value)
        if not metadata.file_path:
            raise ValueError('مسیر دریافت فایل در پیام‌رسان مبدأ موجود نیست.')
        output = BoundedBuffer()
        await self.peer_bot.download(metadata, destination=output)
        extension = PurePosixPath(metadata.file_path.replace('\\', '/')).suffix.lower()
        if extension not in {'.jpg', '.jpeg', '.png', '.pdf', '.mp4', '.ogg', '.mp3', '.zip', '.csv', '.txt', '.webp'}:
            extension = '.jpg' if field == 'photo' else '.bin'
        return BufferedInputFile(output.getvalue(), filename=field + extension)

    async def make_request(self, bot, method, timeout=None):
        values = method.model_dump(warnings=False)
        chat_id = values.get('chat_id')
        # Notifications always travel through the platform of the recipient.
        if isinstance(chat_id, int) and self.peer_bot is not None:
            destination = 'bale' if is_bale_identity(chat_id) else 'telegram'
            if destination != self.platform:
                return await self.peer_bot.session.make_request(self.peer_bot, method, timeout)
        if method.__api_method__ == 'getFile':
            file_id = values.get('file_id', '')
            source = 'bale' if file_id.startswith(BALE_FILE_PREFIX) else 'telegram'
            if source != self.platform:
                result = await self.peer_bot.session.make_request(self.peer_bot, method, timeout)
                return result.model_copy(update={'file_path': 'peerfile:' + result.file_path})
            values['file_id'] = file_id.removeprefix(BALE_FILE_PREFIX)
        for key in ('photo', 'document', 'video', 'audio', 'voice', 'animation'):
            if values.get(key) is not None:
                values[key] = await self._file_value(getattr(method, key), key)
        if values.get('media') and isinstance(values['media'], (dict, list)):
            items = values['media'] if isinstance(values['media'], list) else [values['media']]
            for item in items:
                item['media'] = await self._file_value(item['media'], item.get('type', 'document'))
                if self.platform == 'bale' and item.get('caption'):
                    mode = item.get('parse_mode')
                    if isinstance(mode, Default):
                        mode = getattr(bot.default, mode.name, None)
                    item['caption'] = bale_text(item['caption'], html=mode == 'HTML', entities=item.get('caption_entities'))
                    item['parse_mode'] = None
                    item['caption_entities'] = None
        if values.get('reply_markup'):
            values['reply_markup'] = safe_keyboard(values['reply_markup'], self.platform)
        if self.platform == 'bale':
            for key in ('chat_id', 'user_id', 'from_chat_id'):
                if is_bale_identity(values.get(key)):
                    values[key] = native_identity(values[key])
            for key in ('text', 'caption'):
                if values.get(key):
                    mode = values.get('parse_mode')
                    if isinstance(mode, Default):
                        mode = getattr(bot.default, mode.name, None)
                    values[key] = bale_text(values[key], html=mode == 'HTML', entities=values.get('entities' if key == 'text' else 'caption_entities'))
            # Bale documents plain text; Telegram entities/HTML offsets cannot be reused.
            for key in ('entities', 'caption_entities', 'parse_mode'):
                if key in values:
                    values[key] = None
        method = method.model_copy(update=values)
        try:
            result = await super().make_request(bot, method, timeout)
            if self.platform == 'bale' and method.__api_method__ in {'sendMessage', 'sendPhoto', 'sendDocument', 'sendVideo'}:
                logger.info('Bale API accepted {}; message_id={}', method.__api_method__, getattr(result, 'message_id', None))
            return result
        except TelegramNetworkError:
            # Never include credential-bearing HTTP URLs in exception messages/logs.
            raise TelegramNetworkError(method=method, message='Platform network request failed') from None
