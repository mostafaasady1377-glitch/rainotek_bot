from datetime import datetime,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase,IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,patch
from aiogram import Bot,Dispatcher,Router,F
from aiogram.types import Update
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from bot.services.customer_chat_menu import MenuMessage,InlineNavigationMiddleware,home_keyboard
from bot.services.customer_inbox import InboxStore
from bot.handlers.customer_chat_menu import customer_navigation
from bot.handlers.customer_inbox import inbox_action,send_admin_reply


class InboxTests(TestCase):
    def test_replay_edit_paging_and_reply_claim(self):
        with TemporaryDirectory() as directory:
            store=InboxStore(Path(directory)/'inbox.db')
            message=SimpleNamespace(chat=SimpleNamespace(id=10),from_user=SimpleNamespace(full_name='Customer',username='customer'),
                text='Hello',caption=None,contact=None,content_type='text',message_id=1,date=datetime.now(timezone.utc))
            store.record(message);store.record(message)
            message.text='Edited';store.record(message)
            self.assertEqual(store.threads()[1],1)
            self.assertEqual(store.messages(10)[1],1)
            self.assertEqual(store.messages(10)[0][0]['body'],'Edited')
            self.assertTrue(store.claim_reply(99,20,10))
            self.assertFalse(store.claim_reply(99,20,10))
            with self.assertRaises(ValueError):store.claim_reply(99,21,888)
            store.reply_result(99,20,'sent',100)
            again=InboxStore(store.path)
            self.assertEqual(again.messages(10)[1],1)


class InlineTests(IsolatedAsyncioTestCase):
    async def test_admin_reply_targets_selected_customer_once(self):
        with TemporaryDirectory() as directory:
            store=InboxStore(Path(directory)/'inbox.db')
            incoming=SimpleNamespace(chat=SimpleNamespace(id=10),from_user=SimpleNamespace(full_name='Customer',username=None),text='Help',caption=None,contact=None,content_type='text',message_id=2,date=datetime.now(timezone.utc))
            store.record(incoming)
            admin=SimpleNamespace(telegram_id=99,is_active=True)
            message=SimpleNamespace(message_id=44,text='Answer',reply_to_message=SimpleNamespace(message_id=77),chat=SimpleNamespace(id=99),answer=AsyncMock(),bot=SimpleNamespace(copy_message=AsyncMock(return_value=SimpleNamespace(message_id=500))))
            state=SimpleNamespace(get_data=AsyncMock(return_value={'inbox_target':10,'inbox_prompt':77}),clear=AsyncMock())
            with patch('bot.handlers.customer_inbox.inbox_targets',new=AsyncMock(return_value=None)),patch('bot.handlers.customer_inbox._is_admin',return_value=True),patch('bot.handlers.customer_inbox.InboxStore',return_value=store):
                await send_admin_reply(message,state,admin)
                await send_admin_reply(message,state,admin)
            message.bot.copy_message.assert_awaited_once_with(10,99,44)

    async def test_navigation_edits_existing_message_and_keeps_home(self):
        real=SimpleNamespace(photo=None,edit_text=AsyncMock(),answer=AsyncMock())
        proxy=MenuMessage(real,None)
        await proxy.answer('Section')
        real.answer.assert_not_awaited()
        self.assertEqual(real.edit_text.await_args.args[0],'Section')
        self.assertEqual(real.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[-1][0].callback_data,'cm:home')

    async def test_customer_cannot_read_inbox_or_send_admin_reply(self):
        cb=SimpleNamespace(answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()),data='inbox:thread:10:0')
        user=SimpleNamespace(telegram_id=10,is_active=True,role='customer')
        with patch('bot.handlers.customer_inbox._is_admin',return_value=False):
            await inbox_action(cb,SimpleNamespace(),user)
            self.assertTrue(cb.answer.await_args.kwargs['show_alert'])
            cb.message.answer.assert_not_awaited()
            message=SimpleNamespace(text='Answer',answer=AsyncMock(),bot=SimpleNamespace(copy_message=AsyncMock()))
            state=SimpleNamespace(clear=AsyncMock())
            await send_admin_reply(message,state,user)
            message.bot.copy_message.assert_not_awaited()

    async def test_dispatcher_callback_proxy_keeps_same_message(self):
        class Inject(BaseMiddleware):
            async def __call__(self,handler,event,data):
                data['current_user']=SimpleNamespace(telegram_id=10,is_active=True,role='customer')
                return await handler(event,data)
        router=Router();router.callback_query.register(customer_navigation,F.data.startswith('cm:'))
        dp=Dispatcher();dp.callback_query.middleware(Inject());dp.callback_query.middleware(InlineNavigationMiddleware());dp.include_router(router)
        bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        update=Update.model_validate({'update_id':1,'callback_query':{'id':'q1','chat_instance':'x','data':'cm:support',
            'from':{'id':10,'is_bot':False,'first_name':'Customer'},'message':{'message_id':55,'date':1,'chat':{'id':10,'type':'private'},'from':{'id':123456789,'is_bot':True,'first_name':'Bot'},'text':'Menu'}}})
        calls=[]
        async def api(bot_arg,method,**kwargs):calls.append(method);return True
        with patch.object(bot.session,'make_request',side_effect=api),patch('bot.services.chat_position.position',return_value=True):
            await dp.feed_update(bot,update)
            for action in ('cm:collapse','cm:home'):
                changed=update.model_copy(update={'callback_query':update.callback_query.model_copy(update={'data':action})})
                await dp.feed_update(bot,changed)
        self.assertTrue(any(type(c).__name__=='EditMessageText' and c.message_id==55 for c in calls))
        self.assertFalse(any(type(c).__name__=='SendMessage' for c in calls))
        menus=[c.reply_markup for c in calls if type(c).__name__=='EditMessageText']
        self.assertEqual(menus[-2].inline_keyboard[0][0].callback_data,'cm:home')
        self.assertEqual(menus[-1].inline_keyboard[-1][0].callback_data,'cm:collapse')
        self.assertEqual(menus[-1].inline_keyboard[3][0].callback_data,'cm:ai')
        self.assertIn('🧠',menus[-1].inline_keyboard[3][0].text)
        await bot.session.close()


class PanelTests(IsolatedAsyncioTestCase):
    async def test_panel_button_reuses_message_handler_without_sending(self):
        from bot.services.customer_chat_menu import PanelMessageMiddleware,panel_keyboard
        from bot.handlers.customer_chat_menu import panel_navigation
        from aiogram.types import ReplyKeyboardMarkup,KeyboardButton
        class Inject(BaseMiddleware):
            async def __call__(self,handler,event,data):
                data['current_user']=SimpleNamespace(telegram_id=99,role='seller',is_active=True)
                return await handler(event,data)
        keyboard=panel_keyboard(ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Test panel')]]))
        router=Router()
        router.callback_query.register(panel_navigation,F.data.startswith('panel:'))
        async def selected(message):
            await message.answer('Panel result',reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Back')]]))
        router.message.register(selected,F.text=='Test panel')
        dp=Dispatcher();dp.callback_query.middleware(Inject());dp.callback_query.middleware(InlineNavigationMiddleware())
        dp.message.middleware(Inject());dp.message.middleware(PanelMessageMiddleware());dp.include_router(router)
        bot=Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
        update=Update.model_validate({'update_id':1,'callback_query':{'id':'p1','chat_instance':'x','data':keyboard.inline_keyboard[0][0].callback_data,'from':{'id':99,'is_bot':False,'first_name':'Staff'},'message':{'message_id':55,'date':1,'chat':{'id':99,'type':'private'},'from':{'id':123456789,'is_bot':True,'first_name':'Bot'},'text':'Menu'}}})
        calls=[]
        async def api(bot_arg,method,**kwargs):calls.append(method);return True
        with patch.object(bot.session,'make_request',side_effect=api),patch('bot.services.chat_position.position',return_value=True):await dp.feed_update(bot,update)
        edits=[c for c in calls if type(c).__name__=='EditMessageText']
        self.assertEqual(edits[0].message_id,55)
        self.assertEqual(edits[0].text,'Panel result')
        self.assertTrue(edits[0].reply_markup.inline_keyboard[0][0].callback_data.startswith('panel:'))
        self.assertFalse(any(type(c).__name__=='SendMessage' for c in calls))
        await bot.session.close()

    async def test_foreign_customer_reply_is_denied(self):
        user=SimpleNamespace(telegram_id=99,role='seller',is_active=True)
        message=SimpleNamespace(text='Answer',answer=AsyncMock(),bot=SimpleNamespace(copy_message=AsyncMock()))
        state=SimpleNamespace(get_data=AsyncMock(return_value={'inbox_target':10}),clear=AsyncMock())
        with patch('bot.handlers.customer_inbox.inbox_targets',new=AsyncMock(return_value=[11])):
            await send_admin_reply(message,state,user)
        message.bot.copy_message.assert_not_awaited()


class CaptureTests(IsolatedAsyncioTestCase):
    async def test_capture_precedes_handler_in_any_state(self):
        from bot.services.customer_inbox import InboxCaptureMiddleware
        with TemporaryDirectory() as directory:
            store=InboxStore(Path(directory)/'inbox.db')
            event=SimpleNamespace(chat=SimpleNamespace(id=10,type='private'),from_user=SimpleNamespace(id=10,is_bot=False,full_name='Customer',username='customer'),text='Help during product editing',caption=None,contact=None,content_type='text',message_id=7,date=datetime.now(timezone.utc))
            async def handler(message,data):
                self.assertEqual(store.messages(10)[1],1)
                return 'blocked downstream'
            with patch('bot.services.customer_inbox.InboxStore',return_value=store),patch('bot.config.get_settings',return_value=SimpleNamespace(ADMIN_TELEGRAM_IDS=[99])):
                result=await InboxCaptureMiddleware()(handler,event,{'state':'any state'})
            self.assertEqual(result,'blocked downstream')
            store.claim_reply(99,8,10)
            store.reply_result(99,8,'sent',15,'Support answer')
            rows,total=store.messages(10)
            self.assertEqual(total,2)
            self.assertEqual({r['direction'] for r in rows},{'incoming','outgoing'})
            self.assertIn('Support answer',[r['body'] for r in rows])


class RoleBackTests(IsolatedAsyncioTestCase):
    async def test_seller_back_opens_seller_panel_not_customer(self):
        user=SimpleNamespace(telegram_id=99,role='seller',view_panel='seller',is_active=True,accounting_access=False)
        real=SimpleNamespace(photo=None,answer=AsyncMock(),edit_text=AsyncMock())
        proxy=MenuMessage(real,user)
        await proxy.answer('Subsection')
        self.assertEqual(real.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[-1][0].callback_data,'cm:panelhome')
        callback=SimpleNamespace(answer=AsyncMock(),data='cm:home',message=proxy)
        await customer_navigation(callback,SimpleNamespace(clear=AsyncMock()),user)
        keyboard=real.answer.await_args.kwargs['reply_markup']
        labels=[b.text for row in keyboard.inline_keyboard for b in row]
        self.assertIn('🔗 لینک معرفی من',labels)
        self.assertNotIn('💬 پیام به کارشناس فروش',labels)
        self.assertEqual(real.answer.await_count,2)
