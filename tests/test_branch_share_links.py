import unittest
from bot.services.branch_service import RHINOTECH_BRANCHES
from bot.services.branch_presentation import branch_details
from bot.services.branch_presentation import format_phone
from bot.services.telegram_entities import branch_message_entities


class BranchShareLinksTests(unittest.TestCase):
    def test_phone_numbers_are_plain_text_with_blank_line(self):
        rendered = format_phone('۰۲۱-۲۶۴۰۱۸۵۰ | ۰۹۱۲۰۸۹۴۵۶۰')
        self.assertEqual(rendered, '02126401850\n\n09120894560')
        self.assertNotIn('<a', rendered)

    def test_telegram_receives_phone_entities_with_utf16_offsets(self):
        branch = RHINOTECH_BRANCHES['mirdamad']
        html = '💻\n' + branch_details(branch.name, branch.address, branch.phone + ' | ' + branch.mobile)
        text, entities = branch_message_entities(html)
        phones = [entity for entity in entities if entity.type == 'phone_number']
        self.assertEqual(len(phones), 2)
        for entity, expected in zip(phones, ('02126401850', '09120894560')):
            encoded = text.encode('utf-16-le')
            actual = encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode('utf-16-le')
            self.assertEqual(actual, expected)
        self.assertIn('text_link', [entity.type for entity in entities])

    def test_verified_locations_render_under_the_correct_branch_address(self):
        links = {
            'mirdamad': 'https://nshn.ir/Qbv2Jjexucq3',
            'sadeghiyeh': 'https://nshn.ir/de_bvk9xpxMQkn',
            'heravi': 'https://nshn.ir/f7_bvruZyxRpaA',
            'shahrak': 'https://nshn.ir/62_bvS1NIx4CvQ',
            'fallah': 'https://nshn.ir/62_bvYHI0x4YWP',
        }
        for key, url in links.items():
            with self.subTest(branch=key):
                branch = RHINOTECH_BRANCHES[key]
                rendered = branch_details(branch.name, branch.address, branch.phone, branch.latitude, branch.longitude)
                self.assertIn(f'href="{url}"', rendered)
                self.assertLess(rendered.index(branch.address), rendered.index(url))
                self.assertNotIn('neshan.org/maps/routing', rendered)
                for other in set(links.values()) - {url}:
                    self.assertNotIn(other, rendered)
                if key not in ('mirdamad', 'fallah'):
                    self.assertIn('مجتمع', rendered)

    def test_confirmed_fallah_address_matches_south_sajjad(self):
        branch = RHINOTECH_BRANCHES['fallah']
        self.assertEqual(branch.map_url, 'https://nshn.ir/62_bvYHI0x4YWP')
        self.assertIn('سجاد جنوبی', branch.address)
        self.assertNotIn('سجاد شمالی', branch.address)
