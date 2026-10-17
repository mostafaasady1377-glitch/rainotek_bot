import unittest
from divar_campaign.autofill import fill_text_fields


class Field:
    def __init__(self,value=''):
        self.value=value
    def wait_for(self,**kwargs):
        pass
    def input_value(self):
        return self.value
    def fill(self,value):
        self.value=value


class Page:
    def __init__(self,title='',description=''):
        self.title,self.description=Field(title),Field(description)
    def get_by_placeholder(self,placeholder,**kwargs):
        return self.title if placeholder.startswith('عنوان') else self.description


class AutofillTests(unittest.TestCase):
    def test_reads_back_fields_without_submit_or_photo_upload(self):
        p=Page()
        result=fill_text_fields(p,{'title':'Dell 5420','description':'RAM16 | 40000000 تومان'})
        self.assertEqual(p.title.value,'Dell 5420')
        self.assertIn('40000000',p.description.value)
        self.assertFalse(result['submitted'])
        self.assertFalse(result['paid'])
        self.assertFalse(result['photo_uploaded'])

    def test_conflicting_description_preserves_both_human_fields(self):
        p=Page('', 'Human draft')
        with self.assertRaisesRegex(ValueError,'existing_draft'):
            fill_text_fields(p,{'title':'Dell 5420','description':'Generated draft'})
        self.assertEqual(p.title.value,'')
        self.assertEqual(p.description.value,'Human draft')

    def test_matching_fields_are_idempotent(self):
        p=Page('Dell','Specs')
        fill_text_fields(p,{'title':'Dell','description':'Specs'})
        self.assertEqual(p.title.value,'Dell')
