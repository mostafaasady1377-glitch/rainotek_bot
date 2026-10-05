import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.services.sheet_product_editor import prepare_edits, as_csv
from bot.services.rhinotech_sheet_reader import RhinotechSheetReader as Reader
from bot.services.apps_script_bridge import SheetBridgeError
from bot.handlers.role_panels import confirm_sheet_edit


class ProductEditorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = [['شعبه', 'مدل', 'پردازنده', 'رم', 'هارد', 'صفحه', 'گرافیک', 'قیمت', 'عکس'],
                     ['', 'DELL', '', '', '', '', '', '', ''],
                     ['میرداماد', 'Latitude', 'i5', '8GB', '256 SSD', '14', 'Intel', '50000', ''],
                     ['هروی', 'Latitude', 'i5', '8GB', '256 SSD', '14', 'Intel', '50000', '']]
        self.baseline = Reader.parse_csv(as_csv(self.rows))[0]

    def test_price_updates_all_matching_rows_with_source_units(self):
        edits = prepare_edits(self.rows, self.baseline, 'price', 55000000)
        self.assertEqual([e['row'] for e in edits], [3, 4])
        self.assertTrue(all(e['changes'] == [{'column': 8, 'value': '55000'}] for e in edits))
        self.assertEqual(self.rows[2][7], '50000')

    def test_photo_is_telegram_file_id(self):
        self.assertEqual(prepare_edits(self.rows, self.baseline, 'photo', 'file-id')[0]['changes'][0]['value'], 'tgfile:file-id')

    def test_missing_column_is_safe_failure(self):
        with self.assertRaises(SheetBridgeError):
            prepare_edits(self.rows, self.baseline, 'color', 'black')

    def test_stale_identity_is_rejected(self):
        with self.assertRaises(SheetBridgeError):
            prepare_edits(self.rows, dict(self.baseline, cpu='i7'), 'price', 55000000)

    def test_ambiguous_price_unit_is_rejected(self):
        with self.assertRaises(SheetBridgeError):
            prepare_edits(self.rows, self.baseline, 'price', 55555123)

    async def test_cancel_does_not_write(self):
        from bot.services.edit_safety import new_confirmation
        confirmation = new_confirmation()
        callback = SimpleNamespace(data='panel:sheet:cancel:' + confirmation['edit_token'], answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        state = SimpleNamespace(get_data=AsyncMock(return_value={'sheet_edits': [{}], **confirmation}), clear=AsyncMock())
        with patch('bot.services.sheet_product_editor.apply_product_edit', AsyncMock()) as write:
            await confirm_sheet_edit(callback, state, SimpleNamespace(role='seller', telegram_id=123))
        write.assert_not_awaited()
        state.clear.assert_awaited_once()
