from aiogram import F,Router
from aiogram.fsm.context import FSMContext
from bot.services.customer_chat_menu import open_home

router=Router()


@router.callback_query(F.data.startswith('cm:'))
async def customer_navigation(callback,state:FSMContext,current_user=None):
    await callback.answer()
    await state.clear()
    section=callback.data.split(':')[1]
    message=callback.message
    if section in ('home','panelhome'):
        from bot.handlers.role_panels import _is_staff,show_account_menu
        if _is_staff(current_user) and getattr(current_user,'view_panel',None)!='customer':
            await show_account_menu(message,current_user)
        else:
            await open_home(message,current_user,edit=True)
    elif section=='collapse':
        from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
        await message.edit_text('💻 راینوتک',reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='📋 بازکردن منو',callback_data='cm:home')],
        ]))
    elif section=='catalog':
        from bot.handlers.catalog_browser import show_brands_menu
        await show_brands_menu(message,edit=True)
    elif section=='search':
        from bot.handlers.catalog_browser import open_smart_search_menu
        await open_smart_search_menu(callback)
    elif section=='condition':
        from bot.handlers.catalog_browser import show_condition_menu
        await show_condition_menu(message)
    elif section=='branches':
        from bot.handlers.catalog_browser import render_branches_overview
        await render_branches_overview(message,edit=True)
    elif section=='installment':
        from bot.handlers.online_support import show_installment_models
        await show_installment_models(message)
    elif section=='ai':
        from bot.handlers.ai_hub import AI_MENU
        from bot.services.ai_feature_visits import record_ai_visit
        await record_ai_visit(callback.from_user.id,'ai_menu')
        await message.edit_text('🤖 هوش مصنوعی ai راینوتک',reply_markup=AI_MENU)
    elif section=='support':
        from bot.services.sales_contact import contact_text,expert_chat_keyboard,sales_expert
        expert=sales_expert.get()
        if expert:
            await message.edit_text('💬 پیام به کارشناس فروش شما\nپیام یا سؤال خود را همین‌جا بفرستید؛ کارشناس معرف شما آن را در پنل خودش می‌بیند و می‌تواند از همین بات پاسخ بدهد.'+contact_text(),reply_markup=expert_chat_keyboard())
        elif getattr(current_user,'referrer_telegram_id',None):
            await message.edit_text('کارشناس معرف شما فعلاً فعال یا در دسترس نیست. پیام شما برای پیگیری در اختیار مدیریت قرار می‌گیرد؛ به کارشناس دیگری منتقل نمی‌شود.')
        else:
            await message.edit_text('هنوز کارشناس معرف برای شما ثبت نشده است. پیام خود را همین‌جا بفرستید تا مدیریت برای راهنمایی شما پیگیری کند.')
    elif section=='finance':
        from bot.handlers.financial_archive import finance_home
        await finance_home(message,state,current_user)
    elif section=='admin':
        from bot.handlers.role_panels import _is_admin,_admin_menu,set_view_panel
        if not _is_admin(current_user) or current_user.telegram_id!=7495565146:
            return
        await set_view_panel(current_user,'admin')
        await message.answer('پنل مدیریت',reply_markup=_admin_menu())


@router.callback_query(F.data.regexp(r'^panel:[0-9a-f]{24}$'))
async def panel_navigation(callback,event_router,state,current_user=None):
    from bot.services.customer_chat_menu import panel_label,panel_anchor
    label=panel_label(callback.data.split(':',1)[1])
    if not label:
        await callback.answer('منو را دوباره باز کنید.',show_alert=True)
        return
    await callback.answer()
    original=getattr(callback.message,'original',callback.message)
    synthetic=original.model_copy(update={'from_user':callback.from_user,'text':label,'photo':None,'caption':None})
    dispatcher=event_router
    while dispatcher.parent_router is not None:
        dispatcher=dispatcher.parent_router
    token=panel_anchor.set(original)
    try:
        # Reuse existing filters, FSM and permission checks; no action bypass.
        await dispatcher.propagate_event('message',synthetic,bot=callback.bot,state=state,current_user=current_user)
    finally:
        panel_anchor.reset(token)
