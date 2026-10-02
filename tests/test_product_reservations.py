import unittest
from types import SimpleNamespace

from bot.handlers.catalog_browser import gallery_keyboard, product_gallery_sources
from bot.keyboards.catalog_builder import laptop_detail_keyboard


class ProductReservationTests(unittest.IsolatedAsyncioTestCase):
    def test_ai_product_buttons_are_in_requested_order(self):
        keyboard = laptop_detail_keyboard(42, brand_id=3, from_ai=True)
        self.assertEqual([len(row) for row in keyboard.inline_keyboard], [1, 1, 1, 1])
        self.assertEqual([row[0].callback_data for row in keyboard.inline_keyboard], [
            "order:laptop:42", "tree:brands_root", "branches:laptop:42", "ai:assistant",
        ])
        self.assertNotIn("تصاویر بیشتر", str(keyboard))

    def test_gallery_navigation_advances_and_returns(self):
        self.assertEqual(gallery_keyboard(42, 0, 3).inline_keyboard[0][0].callback_data, "gallery:42:1")
        middle = gallery_keyboard(42, 1, 3)
        self.assertEqual([button.callback_data for button in middle.inline_keyboard[0]], ["gallery:42:0", "gallery:42:2"])
        self.assertIsNone(gallery_keyboard(42, 0, 1))

    async def test_staff_photos_replace_catalog_gallery(self):
        images = [
            SimpleNamespace(image_url="https://example.test/catalog.jpg", telegram_file_id="cached-catalog"),
            SimpleNamespace(image_url="tgfile:real-1", telegram_file_id="real-1"),
            SimpleNamespace(image_url="tgfile:real-2", telegram_file_id="real-2"),
        ]

        class FakeSession:
            async def scalars(self, _statement):
                return SimpleNamespace(all=lambda: images)

        result = await product_gallery_sources(FakeSession(), SimpleNamespace(id=42, image_url=None))
        self.assertEqual(result, ["real-1", "real-2"])
