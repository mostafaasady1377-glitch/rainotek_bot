"""Prepare a fresh source snapshot, then write only after explicit confirmation."""
import csv
import io
import json

from database.models import SheetSyncState
from bot.config import get_settings
from bot.services.apps_script_bridge import SheetBridgeError, configured_bridge
from bot.services.rhinotech_sheet_reader import RhinotechSheetReader as Reader, get_active_spreadsheet_id
from bot.services.stock_lock import stock_lock

HEADERS = {
    'cpu': ('پردازنده',), 'ram': ('رم',), 'storage': ('هارد', 'حافظه'),
    'gpu': ('گرافیک',), 'screen_size': ('صفحه', 'صفحه نمایش'),
    'condition': ('وضعیت', 'وضعیت کالا', 'نوع کالا', 'condition', 'Condition'),
    'color': ('رنگ',), 'price': ('قیمت',),
    'photo': ('عکس', 'تصویر', 'لینک عکس', 'image_url', 'Image'),
}


def as_csv(rows):
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    return output.getvalue()


def prepare_edits(rows, baseline, field, value):
    if field not in HEADERS:
        raise SheetBridgeError('فیلد نامعتبر است.')
    header = next((r for r in rows if 'شعبه' in r and 'مدل' in r), None)
    if not header:
        raise SheetBridgeError('ساختار شیت قابل شناسایی نیست.')
    matches = [i for i, cell in enumerate(header) if cell.strip() in HEADERS[field]]
    if len(matches) != 1:
        raise SheetBridgeError('ستون متناظر این فیلد در شیت موجود یا یکتا نیست؛ هیچ تغییری ثبت نشد.')
    column = matches[0]
    keys = ('brand', 'model', 'cpu', 'ram', 'storage', 'gpu', 'screen')
    items = Reader.parse_csv(as_csv(rows))
    matching = [item for item in items if all(item[k] == baseline.get(k) for k in keys)]
    if not matching:
        raise SheetBridgeError('مدل یا مشخصات شیت تغییر کرده است؛ محصول را دوباره انتخاب کنید.')
    if field in {'cpu', 'ram', 'storage', 'gpu', 'screen_size'}:
        configurations = {tuple(item[k] for k in keys) for item in items
                          if item['brand'] == baseline.get('brand') and item['model'] == baseline.get('model')}
        if len(configurations) > 1:
            raise SheetBridgeError('این مدل چند کانفیگ دارد؛ برای حفظ شناسه و تاریخچه، تغییر کانفیگ نیازمند کد کالای یکتا در شیت است. هیچ تغییری ثبت نشد.')
    if baseline.get('brand') == 'Apple iPhone' and field in {'cpu', 'ram', 'storage', 'gpu', 'color', 'condition'}:
        raise SheetBridgeError('ساختار ستون‌های آیفون با لپ‌تاپ متفاوت است؛ این فیلد باید در شیت ویرایش شود.')
    edits = []
    for item in matching:
        row_number = item['sheet_row']
        original = rows[row_number - 1]
        if column >= len(original):
            raise SheetBridgeError('طول ردیف با ساختار شیت مطابقت ندارد.')
        rendered = 'tgfile:' + str(value) if field == 'photo' else str(value)
        if field == 'price':
            digits = ''.join(c for c in original[column] if c.isdecimal())
            source = int(digits) if digits else 0
            # The reader uses small sheet numbers as thousands of tomans.
            if source < 1000000:
                multiplier = get_settings().SHEET_PRICE_MULTIPLIER
                if multiplier <= 0 or int(value) % multiplier:
                    raise SheetBridgeError('قیمت باید با واحد قیمت شیت قابل ثبت باشد.')
                rendered = str(int(value) // multiplier)
            parsed = int(rendered)
            roundtrip = parsed * get_settings().SHEET_PRICE_MULTIPLIER if 0 < parsed < 1000000 else parsed
            if roundtrip != int(value):
                raise SheetBridgeError('واحد قیمت مبهم است؛ هیچ تغییری ثبت نشد.')
        edits.append(dict(row=row_number, expectedRow=original, changes=[dict(column=column + 1, value=rendered)]))
    return edits


async def prepare_product_edit(session, laptop_id, field, value):
    bridge = configured_bridge()
    async with stock_lock:
        rows = await bridge.read()
        await Reader.sync_sheet_to_database(session, as_csv(rows))
        state = await session.get(SheetSyncState, get_active_spreadsheet_id() + ':0')
        entries = json.loads(state.payload) if state else {}
        entry = next((e for e in entries.values() if e['id'] == laptop_id and not e.get('retired')), None)
        if not entry:
            raise SheetBridgeError('محصول به ردیف معتبر شیت متصل نیست.')
        return prepare_edits(rows, entry['item'], field, value)


async def apply_product_edit(session, edits):
    async with stock_lock:
        # No retries: on timeout the remote write may already have succeeded.
        await configured_bridge().update_many(edits)
        # Invalidate before refresh; failed reconciliation must not show a success.
        Reader._last_content_hash = None
        await Reader.sync_sheet_to_database(session)
