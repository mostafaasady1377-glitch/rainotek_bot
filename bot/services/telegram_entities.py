"""Build Telegram entities for existing HTML messages containing branch phones."""
from html.parser import HTMLParser
import re

from aiogram.types import MessageEntity


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


class _EntityParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.entities: list[MessageEntity] = []
        self.open_tags: list[tuple[str, int, str | None]] = []

    @property
    def text_offset(self):
        return _utf16_length("".join(self.parts))

    def handle_starttag(self, tag, attrs):
        if tag in ("b", "strong", "i", "em", "code", "a"):
            url = dict(attrs).get("href") if tag == "a" else None
            self.open_tags.append((tag, self.text_offset, url))

    def handle_endtag(self, tag):
        if not self.open_tags or self.open_tags[-1][0] != tag:
            return
        _, start, url = self.open_tags.pop()
        length = self.text_offset - start
        if not length:
            return
        kind = {"b": "bold", "strong": "bold", "i": "italic", "em": "italic", "code": "code", "a": "text_link"}[tag]
        self.entities.append(MessageEntity(type=kind, offset=start, length=length, url=url))

    def handle_data(self, data):
        self.parts.append(data)


def branch_message_entities(html_text: str) -> tuple[str, list[MessageEntity]]:
    parser = _EntityParser()
    parser.feed(html_text)
    text = "".join(parser.parts)
    for match in re.finditer(r"(?<!\d)(?:021\d{8}|09\d{9})(?!\d)", text):
        parser.entities.append(MessageEntity(
            type="phone_number",
            offset=_utf16_length(text[:match.start()]),
            length=_utf16_length(match.group()),
        ))
    return text, sorted(parser.entities, key=lambda entity: (entity.offset, -entity.length))
