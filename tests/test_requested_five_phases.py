import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database.models import Base, Laptop, LaptopBrand
from bot.services.ai_search_service import AISearchService
from bot.services.rhinotech_sheet_reader import RhinotechSheetReader
from bot.services.product_condition import normalize_condition
from bot.services.branch_presentation import branch_details
from bot.keyboards.reply_menus import main_menu_kb
from bot.keyboards.catalog_builder import smart_search_menu_keyboard, branches_menu_keyboard
from bot.handlers.catalog_browser import format_price

class FivePhasesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory=async_sessionmaker(self.engine,expire_on_commit=False)
    async def asyncTearDown(self):
        await self.engine.dispose()
    async def test_ram_brand_storage_and_budget_match_real_rows(self):
        async with self.factory() as s:
            brand=LaptopBrand(name='Dell');s.add(brand);await s.flush()
            s.add_all([Laptop(brand_id=brand.id,model='5400',cpu='i5',ram='8GB',storage='256 SSD',price=35123456,status='active'),Laptop(brand_id=brand.id,model='wrong RAM',cpu='i5',ram='128GB',storage='256 SSD',price=30000000,status='active'),Laptop(brand_id=brand.id,model='unknown price',cpu='i5',ram='8GB',storage='256 SSD',price=0,status='active')]);await s.commit()
            found=await AISearchService.smart_search(s,'برند دل رم ۸ i5 هارد 256 تا ۵۰ میلیون',limit=1)
            self.assertEqual([x['model'] for x in found],['5400'])
            self.assertEqual(found[0]['price'],35123456)
        self.assertEqual(format_price(35123456),'35,123,456 تومان')
    def test_explicit_sheet_condition_and_image_are_preserved(self):
        source='شعبه,مدل,پردازنده,رم,هارد,صفحه,گرافیک,,قیمت,وضعیت,عکس\n,DELL,,,,,,,,,\nمیرداماد,5400,i5,8,256,14,Intel,,35000,آکبند,https://example.org/photo.jpg'
        item=RhinotechSheetReader.parse_csv(source)[0]
        self.assertEqual(normalize_condition(item['condition']),'نو')
        self.assertEqual(item['image_url'],'https://example.org/photo.jpg')
        self.assertEqual(item['price_tomans'],35000000)
        self.assertIsNone(normalize_condition('ثبت نشده'))
        self.assertEqual(normalize_condition('استوک سالم'),'استوک')
    def test_main_menu_and_search_only_requested_entries(self):
        labels=[b.text for row in main_menu_kb().keyboard for b in row]
        self.assertNotIn('🛒 پیگیری سفارش من',labels)
        self.assertNotIn('ℹ️ راهنمای خرید و تماس',labels)
        self.assertEqual(labels[-1],'🤖 هوش مصنوعی AI')
        self.assertIn('consultation:online',[b.callback_data for row in smart_search_menu_keyboard().inline_keyboard for b in row])
        self.assertNotIn('branches:maps_list',[b.callback_data for row in branches_menu_keyboard([]).inline_keyboard for b in row])
    def test_branch_phone_and_neshan_are_inside_text(self):
        text=branch_details('شعبه میرداماد','آدرس','02126401850 | 09120894560',35.7592,51.4285)
        self.assertIn('02126401850',text)
        self.assertIn('09120894560',text)
        self.assertNotIn('<code>',text)
        self.assertNotIn('021-',text)
        self.assertIn('href="https://nshn.ir/Qbv2Jjexucq3"',text)
        self.assertNotIn('neshan.org/maps/routing/car/destination/',text)
        self.assertLess(text.index('📍 نشانی:'), text.index('https://nshn.ir/'))
        self.assertNotIn('maps.google',text)
    async def test_catalog_root_has_no_extra_search_or_branches(self):
        from bot.handlers.catalog_browser import show_brands_menu
        async with self.factory() as s:
            b=LaptopBrand(name='Dell');s.add(b);await s.flush();s.add(Laptop(brand_id=b.id,model='5400',status='active'));await s.commit()
        msg=SimpleNamespace(answer=AsyncMock())
        with patch('bot.handlers.catalog_browser.AsyncSessionLocal',self.factory):
            await show_brands_menu(msg)
        buttons=msg.answer.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(len(buttons),1)
        self.assertTrue(buttons[0][0].callback_data.startswith('tree:brand:'))

    async def test_local_voice_transcript_uses_sheet_search_and_product_links(self):
        from bot.handlers.voice_assistant import handle_voice
        message=SimpleNamespace(voice=SimpleNamespace(file_id='file',file_size=100),answer=AsyncMock())
        bot=SimpleNamespace(get_file=AsyncMock(return_value=SimpleNamespace(file_path='voice.ogg')),download_file=AsyncMock())
        result=[dict(id=42,brand='Dell',model='5400',price=35123456)]
        with patch('bot.config.get_settings',return_value=SimpleNamespace(GROQ_API_KEY='')),patch('bot.services.local_speech.transcribe_local',AsyncMock(return_value='رم هشت برند دل')),patch('bot.services.ai_search_service.AISearchService.smart_search',AsyncMock(return_value=result)) as search,patch('bot.handlers.voice_assistant.AsyncSessionLocal',self.factory):
            await handle_voice(message,bot)
        self.assertEqual(search.call_args.args[1],'رم هشت برند دل')
        button=message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
        self.assertEqual(button.callback_data,'tree:laptop:42')
        self.assertIn('35,123,456',button.text)
    def test_spoken_numbers_and_cpu_map_without_wrong_budget(self):
        intent=AISearchService.parse_query_intent('برند دل رم هشت کور آی پنج هارد دویست و پنجاه و شش تا پنجاه میلیون')
        self.assertEqual(intent['min_ram'],8)
        self.assertEqual(intent['cpu_family'],'i5')
        self.assertEqual(intent['storage_capacity'],256)
        self.assertEqual(intent['max_price'],50000000)
        self.assertIsNone(intent['min_price'])

    def test_inferred_condition_does_not_override_source_or_claim_new(self):
        from bot.services.product_condition import condition_info
        old=SimpleNamespace(condition='ثبت نشده',model='650 G3',cpu='i7-7820HQ')
        self.assertEqual(condition_info(old)[0],'استوک')
        self.assertTrue(condition_info(old)[2])
        old.condition='نو آکبند'
        self.assertEqual(condition_info(old),('نو','نو آکبند',False))
        modern=SimpleNamespace(condition='ثبت نشده',model='2025',cpu='i7-1360P')
        self.assertEqual(condition_info(modern)[0], 'نو')
        self.assertIn('نو بودن دستگاه در شیت تأیید نشده', condition_info(modern)[1])
    async def test_brand_fallback_photo_has_clear_caption_and_full_specs(self):
        from bot.handlers.catalog_browser import show_laptop_card
        async with self.factory() as s:
            b=LaptopBrand(name='MSI Gaming');s.add(b);await s.flush()
            laptop=Laptop(brand_id=b.id,model='unknown configuration',cpu='i7-7700HQ',ram='8',status='active',price=50000000,condition='ثبت نشده');s.add(laptop);await s.commit();id=laptop.id
        message=SimpleNamespace(answer_photo=AsyncMock(),answer=AsyncMock())
        with patch('bot.handlers.catalog_browser.AsyncSessionLocal',self.factory):
            await show_laptop_card(message,id)
        self.assertIn('عکس دستگاه انتخاب‌شده نیست',message.answer_photo.call_args.kwargs['caption'])
        self.assertIn('استوک',message.answer.call_args.args[0])
        self.assertNotIn('احتمالی',message.answer.call_args.args[0])
        self.assertIn('50,000,000',message.answer.call_args.args[0])
