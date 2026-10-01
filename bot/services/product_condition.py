import re

def normalize_condition(value):
    value = (value or '').replace('ي', 'ی').replace('ك', 'ک').casefold()
    if any(word in value for word in ('استوک', 'دست دوم', 'کارکرده', 'stock', 'used', 'refurbished')):
        return 'استوک'
    if any(word in value for word in ('آکبند', 'اکبند', 'new', 'brand new')) or re.search(r'(?<!\w)نو(?!\w)', value):
        return 'نو'
    return None


def condition_info(laptop):
    explicit=normalize_condition(laptop.condition)
    if explicit:
        return explicit, laptop.condition, False
    model=(laptop.model or '').translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789'))
    years=[int(year) for year in re.findall(r'(?<!\d)(20[0-2]\d)(?!\d)',model)]
    cpu=(laptop.cpu or '').lower()
    match=re.search(r'i[3579]\s*[- ]?\s*(\d{1,5})',cpu)
    generation=None
    if match:
        digits=match.group(1)
        generation = int(digits[:2]) if len(digits)>=4 and digits[:2] in ('10','11','12','13','14') else int(digits[:-3]) if len(digits)>=4 else int(digits) if len(digits)<=2 else None
    if (years and max(years)<=2021) or (not years and generation and 1<=generation<=10):
        return 'استوک', 'استوک — دسته‌بندی براساس قدمت مدل؛ وضعیت دستگاه نیازمند تأیید فروشگاه', True
    if (years and max(years)>=2022) or (not years and generation and generation>=11):
        return 'نو', 'مدل جدید — نو بودن دستگاه در شیت تأیید نشده', True
    details = (laptop.condition or '').strip()
    label = 'وضعیت قطعی نیازمند تأیید فروشگاه'
    if details and details != 'ثبت نشده':
        label += ' — ' + details
    return None, label, False
