from __future__ import annotations

from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from bot.services.ai_voice_service import AIVoiceService
from bot.services.inventory_service import InventoryService
from database.session import AsyncSessionLocal

router = Router()


def format_voice_results(filters: dict[str, str | None], laptops, branch_details) -> str:
    requested = "، ".join(f"{key}: {escape(value)}" for key, value in filters.items() if value)
    lines = [f"لپ‌تاپ‌های منطبق با درخواست صوتی شما ({requested}):"]
    if not laptops:
        return "هیچ لپ‌تاپی با مشخصات گفته‌شده پیدا نشد."

    for laptop in laptops:
        lines.append(
            f"\nمدل: {escape(laptop.brand.name)} {escape(laptop.model)}\n"
            f"مشخصات: {escape(laptop.cpu or '-')} / {escape(laptop.ram or '-')} RAM / "
            f"{escape(laptop.gpu or '-')} / {escape(laptop.storage or '-')}"
        )
        available_branches = [
            branch for branch in branch_details.get(laptop.id, [])
            if int(branch["available"]) > 0
        ]
        if not available_branches:
            lines.append("موجودی قابل فروش در شعب ثبت نشده است.")
            continue
        for branch in available_branches:
            lines.append(
                f"📍 {escape(str(branch['name']))} ({branch['available']} عدد)\n"
                f"تلفن: {escape(str(branch['phone'] or 'ثبت نشده'))}\n"
                f"آدرس: {escape(str(branch['address'] or 'ثبت نشده'))}"
            )
    return "\n".join(lines)[:4000]


@router.message(F.voice)
async def handle_voice(message: Message, bot: Bot) -> None:
    try:
        voice = message.voice
        if voice is None:
            return
        if voice.file_size is not None and voice.file_size > 20 * 1024 * 1024:
            await message.answer("حجم ویس بیش از حد مجاز است؛ لطفاً فایل کوتاه‌تری بفرستید.")
            return
        file_info = await bot.get_file(voice.file_id)
        if not file_info.file_path:
            await message.answer("فایل ویس از تلگرام دریافت نشد؛ دوباره ارسال کنید.")
            return
        audio_buffer = BytesIO()
        await bot.download_file(file_info.file_path, destination=audio_buffer)
        audio_buffer.seek(0)

        from bot.config import get_settings
        if get_settings().GROQ_API_KEY:
            transcript = await AIVoiceService().transcribe_audio(audio_buffer.getvalue())
        else:
            from bot.services.local_speech import transcribe_local
            transcript = await transcribe_local(audio_buffer.getvalue())
        from bot.services.ai_search_service import AISearchService
        from bot.handlers.catalog_browser import format_price
        async with AsyncSessionLocal() as session:
            results = await AISearchService.smart_search(session, transcript, limit=20)
        buttons = [[InlineKeyboardButton(text=f"{r['brand']} {r['model']} — {format_price(r['price'])}"[:60], callback_data=f"tree:laptop:{r['id']}")] for r in results]
        await message.answer(f"🎙 {escape(transcript)}\n\n" + ("مدل‌های منطبق با موجودی شیت:" if results else "مدل منطبق در شیت پیدا نشد."), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None)
    except ValueError as exc:
        await message.answer(str(exc))
    except RuntimeError as exc:
        await message.answer(str(exc))
    except Exception:
        await message.answer("در پردازش ویس مشکلی پیش آمد. لطفاً دوباره تلاش کنید یا درخواست را متنی بفرستید.")
