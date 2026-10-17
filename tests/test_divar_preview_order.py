from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,patch
from types import SimpleNamespace
from aiogram.types import InputMediaPhoto
from bot.handlers.divar_campaigns import show_personal
from bot.services.customer_chat_menu import MenuMessage

class PreviewOrderTests(IsolatedAsyncioTestCase):
    async def test_gallery_then_text_and_bottom_buttons(self):
        calls=[]
        async def album(*args,**kwargs):calls.append(('photo',args,kwargs))
        async def answer(*args,**kwargs):calls.append(('text',args,kwargs))
        real=SimpleNamespace(photo=None,answer_photo=AsyncMock(side_effect=album),answer_media_group=AsyncMock(),answer=AsyncMock(side_effect=answer),edit_text=AsyncMock())
        user=SimpleNamespace(telegram_id=11,role='seller',view_panel='seller',is_active=True)
        ad=dict(title='Shop ad',description='Example stock',blockers=[],product_id=1)
        row=dict(id=1,digest='a'*64,state='suggested',ad=ad)
        store=SimpleNamespace(allocate_daily=lambda **kwargs:None,daily_rows=lambda *args,**kwargs:[row])
        photos=[{'path':__file__} for _ in range(15)]
        with patch('bot.services.divar_recommendations.RecommendationStore',return_value=store),patch('bot.services.divar_gallery.gallery_files',return_value=photos):
            await show_personal(MenuMessage(real,user),user,index=0)
        self.assertEqual([c[0] for c in calls],['photo','text'])
        real.answer_media_group.assert_not_awaited()
        labels=[b.text for r in calls[-1][2]['reply_markup'].inline_keyboard for b in r]
        self.assertIn('انتشار آگهی',labels)
        self.assertIn('✏️ ویرایش آگهی',labels)
        self.assertNotIn('بازگشت به آگهی‌های امروز',labels)
        self.assertNotIn('آرشیو آگهی‌ها',labels)
        self.assertIn('🔙 بازگشت به پنل کارشناسان فروش',labels)
        real.edit_text.assert_not_awaited()

    async def test_gallery_navigation_edits_same_photo(self):
        from bot.handlers.divar_campaigns import personal_action
        real=SimpleNamespace(edit_media=AsyncMock(),answer=AsyncMock())
        callback=SimpleNamespace(data='dr:gallery:42:1',answer=AsyncMock(),message=real)
        store=SimpleNamespace(daily_rows=lambda *a,**k:[dict(id=42,state='suggested',ad=dict(product_id=7))])
        with patch('bot.handlers.divar_campaigns.can_view',return_value=True),patch('bot.services.divar_recommendations.RecommendationStore',return_value=store),patch('bot.services.divar_gallery.gallery_files',return_value=[{'path':__file__}]*15):
            await personal_action(callback,SimpleNamespace(),SimpleNamespace(telegram_id=11))
        real.edit_media.assert_awaited_once()
        real.answer.assert_not_awaited()
        self.assertIn('2 از 15',real.edit_media.await_args.args[0].caption)
        from aiogram.exceptions import TelegramBadRequest
        from aiogram.methods import EditMessageMedia
        real.edit_media.side_effect=TelegramBadRequest(method=EditMessageMedia(media=InputMediaPhoto(media='photo')),message='message is not modified')
        with patch('bot.handlers.divar_campaigns.can_view',return_value=True),patch('bot.services.divar_recommendations.RecommendationStore',return_value=store),patch('bot.services.divar_gallery.gallery_files',return_value=[{'path':__file__}]*15):
            await personal_action(callback,SimpleNamespace(),SimpleNamespace(telegram_id=11))
