import re
import time
import secrets


def parse_price(value):
    text = str(value).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    if not re.fullmatch(r'(?:[0-9]+|[1-9][0-9]{0,2}(?:[,٬][0-9]{3})+)', text):
        raise ValueError('قیمت را فقط به صورت عدد کامل و مثبت به تومان وارد کنید؛ مثلاً ۵۰٬۰۰۰٬۰۰۰.')
    number = int(text.replace(',', '').replace('٬', ''))
    if not 0 < number <= 999999999999999:
        raise ValueError('قیمت خارج از محدودهٔ معتبر است.')
    return number


def new_confirmation():
    return dict(edit_token=secrets.token_hex(8), edit_expires=time.time() + 600)


def valid_confirmation(data, token):
    return bool(token and data.get('edit_token') == token and data.get('edit_expires', 0) > time.time())
