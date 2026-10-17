from aiogram.types import BotCommand, MenuButtonCommands


async def configure_telegram_menu(bot):
    commands=await bot.get_my_commands()
    if not any(command.command=='menu' for command in commands):
        commands.append(BotCommand(command='menu',description='نمایش منوی اصلی'))
        await bot.set_my_commands(commands)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
