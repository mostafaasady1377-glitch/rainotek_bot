"""Track the last observed message per bot/chat, including album messages."""
import sqlite3
from contextlib import closing
from pathlib import Path

PATH = Path('scratch/chat-position.sqlite3')


def position(message, *, record=False):
    bot = getattr(message, 'bot', None)
    bot_id = getattr(bot, 'id', None)
    chat_id = getattr(getattr(message, 'chat', None), 'id', None)
    message_id = getattr(message, 'message_id', None)
    if not all(isinstance(v, int) for v in (bot_id, chat_id, message_id)):
        return None
    PATH.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(PATH, timeout=10)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_tail(bot INTEGER, chat INTEGER, message INTEGER, PRIMARY KEY(bot,chat))')
        if record:
            db.execute('INSERT INTO chat_tail VALUES(?,?,?) ON CONFLICT(bot,chat) DO UPDATE SET message=max(message,excluded.message)', (bot_id,chat_id,message_id))
        row = db.execute('SELECT message FROM chat_tail WHERE bot=? AND chat=?', (bot_id,chat_id)).fetchone()
    # After a restart without known history, move an old menu once to the bottom.
    return bool(row and row[0] == message_id)


async def track_outgoing(make_request, bot, method):
    result = await make_request(bot, method)
    for message in result if isinstance(result, list) else [result]:
        if getattr(message, 'chat', None) is not None:
            position(message, record=True)
    return result
