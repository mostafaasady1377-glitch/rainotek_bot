import json
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.methods import SendMessage, SendPhoto, GetUpdates, GetMe, EditMessageMedia, GetFile
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, MessageEntity, File

from bot.services.bale_transport import PlatformSession, normalize_bale_payload, bale_text, safe_keyboard, BoundedBuffer
from bot.services.platform_identity import bale_identity, native_identity, is_bale_identity


class BaleTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = PlatformSession('bale', 'local', 'peer')
        self.bot = Bot('123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi', session=self.session)
        self.session.peer_bot = SimpleNamespace(session=SimpleNamespace(make_request=AsyncMock()), download=AsyncMock(), get_file=AsyncMock(return_value=File(file_id='telegram-file',file_unique_id='unique',file_path='photos/test.jpg')))

    async def asyncTearDown(self):
        await self.bot.session.close()

    def test_identity_and_contact_provenance(self):
        for identity in (1, 2047791995, -1234567):
            self.assertNotEqual(bale_identity(identity), identity)
            self.assertTrue(is_bale_identity(bale_identity(identity)))
            self.assertEqual(native_identity(bale_identity(identity)), identity)
        message = normalize_bale_payload(dict(message_id=1, **{'from': {'id': 10}}, chat={'id': 10}, contact={'user_id': 10}, photo=[{'file_id': 'abc'}]))
        self.assertEqual(message['from']['id'], message['chat']['id'])
        self.assertEqual(message['contact']['user_id'], message['chat']['id'])
        self.assertEqual(message['photo'][0]['file_id'], 'balefile:abc')
        self.assertEqual(message['message_id'], 1)

    async def test_output_is_bale_native_and_plain_text(self):
        with patch.object(AiohttpSession, 'make_request', AsyncMock(return_value=True)) as request:
            await self.session.make_request(self.bot, SendMessage(chat_id=bale_identity(10), text='<b>دکمهٔ رسمی تلگرام</b>', parse_mode='HTML'))
            converted = request.call_args.args[1]
            self.assertEqual(converted.chat_id, 10)
            self.assertEqual(converted.text, 'دکمهٔ رسمی بله')
            self.assertIsNone(converted.parse_mode)
            await self.session.make_request(self.bot, SendMessage(chat_id=bale_identity(10), text='test', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='click',callback_data='test')]])))
            self.assertEqual(request.call_args.args[1].reply_markup['inline_keyboard'][0][0]['callback_data'], 'test')
        self.session.peer_bot.session.make_request.assert_not_awaited()

    async def test_cross_platform_notification_routes_to_peer(self):
        method = SendMessage(chat_id=10, text='test')
        await self.session.make_request(self.bot, method)
        self.session.peer_bot.session.make_request.assert_awaited_once()

    async def test_cross_platform_photo_is_uploaded_not_reused(self):
        async def download(value, destination):
            destination.write(b'image bytes')
            destination.seek(0)
        self.session.peer_bot.download.side_effect = download
        with patch.object(AiohttpSession, 'make_request', AsyncMock(return_value=True)) as request:
            await self.session.make_request(self.bot, SendPhoto(chat_id=bale_identity(10), photo='telegram-file'))
            converted = request.call_args.args[1]
            self.assertIsInstance(converted.photo, BufferedInputFile)
            self.assertEqual(converted.photo.data, b'image bytes')
            self.session.peer_bot.get_file.return_value = File(file_id='pdf-file',file_unique_id='unique',file_path='documents/invoice.pdf')
            document = await self.session._file_value('pdf-file', 'document')
            self.assertEqual(document.filename, 'document.pdf')
            await self.session.make_request(self.bot, SendPhoto(chat_id=bale_identity(10), photo='balefile:bale-file'))
            self.assertEqual(request.call_args.args[1].photo, 'bale-file')

    async def test_gallery_edit_media_strips_native_file_provenance(self):
        with patch.object(AiohttpSession, 'make_request', AsyncMock(return_value=True)) as request:
            await self.session.make_request(self.bot, EditMessageMedia(chat_id=bale_identity(10), message_id=5, media=InputMediaPhoto(media='balefile:second-photo', caption='<b>تصویر</b>', parse_mode='HTML')))
            media = request.call_args.args[1].media
            self.assertEqual(media['media'], 'second-photo')
            self.assertEqual(media['caption'], 'تصویر')
            self.assertIsNone(media['parse_mode'])

    async def test_plain_specs_and_branch_map_entities_survive(self):
        text = 'مدل <laptop> · مسیریابی'
        with patch.object(AiohttpSession, 'make_request', AsyncMock(return_value=True)) as request:
            await self.session.make_request(self.bot, SendMessage(chat_id=bale_identity(10), text=text, parse_mode=None, entities=[MessageEntity(type='text_link', offset=0, length=1, url='https://nshn.ir/branch')]))
            converted = request.call_args.args[1]
            self.assertIn('<laptop>', converted.text)
            self.assertIn('https://nshn.ir/branch', converted.text)
            self.assertIsNone(converted.entities)

    async def test_cross_platform_download_uses_source_endpoint(self):
        self.session.peer_bot.session.make_request.return_value = File(file_id='telegram-file', file_unique_id='unique', file_path='photos/test.jpg')
        result = await self.session.make_request(self.bot, GetFile(file_id='telegram-file'))
        self.assertEqual(result.file_path, 'peerfile:photos/test.jpg')
        self.assertIn('api.telegram.org/file/botpeer/', self.session.api.file_url('local',result.file_path))
        self.assertNotIn('botlocal', self.session.api.file_url('local',result.file_path))

    def test_keyboard_and_link_conversion(self):
        self.assertIn('https://ble.ir/Rinotek_bot?start=R1', bale_text('<a href="https://t.me/Rinotek_bot?start=R1">لینک</a>'))
        self.assertNotIn('tg://', bale_text(f'<a href="tg://user?id={bale_identity(10)}">چت</a>'))
        self.assertIn('چت تلگرام', bale_text('<a href="https://t.me/TGexpert">چت تلگرام</a>'))
        rows = safe_keyboard({'inline_keyboard': [[{'text':'chat', 'url':f'tg://user?id={bale_identity(10)}'}], [{'text':'back', 'callback_data':'back'}]]}, 'bale')
        self.assertEqual(len(rows['inline_keyboard']), 1)
        buffer = BoundedBuffer()
        buffer.seek(30*1024*1024)
        with self.assertRaises(ValueError):
            buffer.write(b'x')

    def test_actual_update_response_validation(self):
        message = dict(message_id=1, date=1, chat={'id':10,'type':'private'}, **{'from':{'id':10,'is_bot':False,'first_name':'Test'}}, text='/start')
        result = self.session.check_response(self.bot, GetUpdates(), 200, json.dumps({'ok':True,'result':[{'update_id':1,'message':message}]}))
        self.assertEqual(result.result[0].message.from_user.id, bale_identity(10))
        callback = {'id':'callback-1','from':{'id':10,'is_bot':False,'first_name':'Test'},'message':message,'data':'installment:model:1'}
        result = self.session.check_response(self.bot, GetUpdates(), 200, json.dumps({'ok':True,'result':[{'update_id':2,'callback_query':callback}]}))
        self.assertEqual(result.result[0].callback_query.from_user.id, bale_identity(10))
        self.assertEqual(result.result[0].callback_query.chat_instance, 'bale:10')
        result = self.session.check_response(self.bot, GetMe(), 200, json.dumps({'ok':True,'result':{'id':123456,'is_bot':True,'first_name':'Bot'}}))
        self.assertEqual(result.result.id, 123456)

    async def test_bale_and_telegram_same_numeric_user_cannot_share_admin(self):
        from bot.config import Settings
        from database.models import Base
        from database.users import ensure_user
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            settings = Settings(ADMIN_TELEGRAM_IDS=[bale_identity(10)])
            async with factory() as session:
                telegram = await ensure_user(session, 10, 'same', 'Test', settings=settings)
                bale = await ensure_user(session, bale_identity(10), 'same', 'Test', settings=settings)
                self.assertEqual(telegram.role, 'customer')
                self.assertEqual(bale.role, 'admin')
                self.assertNotEqual(bale.id, telegram.id)
        finally:
            await engine.dispose()
