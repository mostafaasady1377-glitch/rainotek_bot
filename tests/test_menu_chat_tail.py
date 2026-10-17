from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from bot.services.chat_position import position, track_outgoing
from bot.services.customer_chat_menu import MenuMessage


class ChatTailTests(IsolatedAsyncioTestCase):
    async def test_expert_root_panel_is_always_last_and_clears_legacy_keyboard(self):
        from bot.handlers.role_panels import _staff_menu
        from aiogram.types import ReplyKeyboardRemove
        cleanup=SimpleNamespace(delete=AsyncMock())
        real=SimpleNamespace(photo=None,reply_markup=None,answer=AsyncMock(side_effect=[cleanup,None]),edit_text=AsyncMock())
        user=SimpleNamespace(telegram_id=11,role='seller',view_panel='seller')
        await MenuMessage(real,user).answer('Expert panel',reply_markup=_staff_menu(user))
        self.assertIsInstance(real.answer.await_args_list[0].kwargs['reply_markup'],ReplyKeyboardRemove)
        buttons=real.answer.await_args_list[1].kwargs['reply_markup'].inline_keyboard
        self.assertFalse(any(b.callback_data=='cm:panelhome' for row in buttons for b in row))
        cleanup.delete.assert_awaited_once()
        real.edit_text.assert_not_awaited()

    async def test_old_menu_moves_below_album_and_retires_buttons(self):
        with TemporaryDirectory() as directory, patch('bot.services.chat_position.PATH', Path(directory)/'tail.db'):
            def msg(mid, photo=None):
                return SimpleNamespace(bot=SimpleNamespace(id=1),chat=SimpleNamespace(id=7),message_id=mid,
                    photo=photo,reply_markup=object(),answer=AsyncMock(),edit_text=AsyncMock(),edit_reply_markup=AsyncMock())
            old=msg(10)
            album=[msg(11,['photo']),msg(12,['photo'])]
            await track_outgoing(AsyncMock(return_value=album),old.bot,None)
            new=msg(13)
            old.answer.return_value=new
            proxy=MenuMessage(old,None)
            await proxy.answer('New menu')
            old.answer.assert_awaited_once()
            old.edit_text.assert_not_awaited()
            old.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
            position(new,record=True)
            await proxy.answer('Next section')
            new.edit_text.assert_awaited_once()

    async def test_photo_is_never_changed_into_menu_and_chat_isolation(self):
        with TemporaryDirectory() as directory, patch('bot.services.chat_position.PATH', Path(directory)/'tail.db'):
            photo=SimpleNamespace(bot=SimpleNamespace(id=1),chat=SimpleNamespace(id=7),message_id=12,
                photo=['photo'],reply_markup=None,answer=AsyncMock(),edit_caption=AsyncMock())
            position(photo,record=True)
            await MenuMessage(photo,None).answer('Menu')
            photo.answer.assert_awaited_once()
            photo.edit_caption.assert_not_awaited()
            other=SimpleNamespace(bot=SimpleNamespace(id=2),chat=photo.chat,message_id=12)
            self.assertFalse(position(other))

    async def test_failed_delivery_keeps_old_buttons(self):
        real=SimpleNamespace(photo=None,reply_markup=object(),answer=AsyncMock(side_effect=RuntimeError('offline')),
            edit_reply_markup=AsyncMock())
        with self.assertRaises(RuntimeError):
            await MenuMessage(real,None,editable=False).answer('Menu')
        real.edit_reply_markup.assert_not_awaited()
