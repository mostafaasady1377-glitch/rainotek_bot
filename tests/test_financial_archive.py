import unittest
import io
import zipfile
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, User, FinancialDocument
from bot.handlers.financial_archive import authorized, upload_document, finance_action, list_documents


class FinancialArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.actor = User(telegram_id=80, role='customer', is_active=True, accounting_access=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_permission_is_separate_and_inactive_denied(self):
        self.assertTrue(authorized(self.actor))
        self.assertFalse(authorized(User(telegram_id=81, role='seller', accounting_access=False)))
        self.assertFalse(authorized(User(telegram_id=81, role='customer', accounting_access=True, is_active=False)))

    async def test_photo_upload_persists_once_and_zip_contains_file(self):
        message = N(chat=N(id=80, type='private'), message_id=10, photo=[N(file_id='photo-id', file_size=10)], document=None, caption='invoice note', answer=AsyncMock())
        state = N(get_data=AsyncMock(return_value={'finance_category':'invoice'}))
        with patch('bot.handlers.financial_archive.AsyncSessionLocal', self.factory):
            await upload_document(message, state, self.actor)
            await upload_document(message, state, self.actor)
            async with self.factory() as session:
                documents, total, page = await list_documents(session, 'invoice', 0)
                self.assertEqual(total, 1)
                self.assertEqual(documents[0].caption, 'invoice note')
            async def download(file_id, destination):
                destination.write(b'photo-content')
            callback = N(data='fin:zip:invoice:0', answer=AsyncMock(), bot=N(download=AsyncMock(side_effect=download)), message=N(chat=N(type='private'), answer=AsyncMock(), answer_document=AsyncMock()))
            await finance_action(callback, state, self.actor)
            file = callback.message.answer_document.await_args.args[0]
            with zipfile.ZipFile(io.BytesIO(file.data)) as archive:
                self.assertIn('1.jpg', archive.namelist())
                self.assertEqual(archive.read('1.jpg'), b'photo-content')

    async def test_group_and_unauthorized_access_do_not_send_files(self):
        for user, chat_type in [(User(telegram_id=81, accounting_access=False), 'private'), (self.actor, 'group')]:
            callback = N(data='fin:file:1', answer=AsyncMock(), message=N(chat=N(type=chat_type), answer_document=AsyncMock(), answer_photo=AsyncMock()))
            await finance_action(callback, N(), user)
            callback.message.answer_document.assert_not_awaited()
            callback.message.answer_photo.assert_not_awaited()

    async def test_accounting_grant_and_revoke_are_persistent(self):
        from bot.handlers.financial_archive import change_finance_access
        from bot.config import Settings
        async with self.factory() as session:
            user = User(telegram_id=80, role='customer', is_active=True)
            session.add(user)
            await session.commit()
            identity = user.id
        with patch('bot.handlers.financial_archive.AsyncSessionLocal', self.factory), patch('bot.handlers.role_panels.get_settings', return_value=Settings(ADMIN_TELEGRAM_IDS=[90])):
            for value in ('1','0'):
                callback = N(data=f'finaccess:{identity}:{value}', answer=AsyncMock(), message=N(edit_reply_markup=AsyncMock()))
                await change_finance_access(callback, User(telegram_id=90, role='admin', is_active=True))
                async with self.factory() as session:
                    saved = await session.get(User, identity)
                    self.assertEqual(saved.accounting_access, value == '1')
