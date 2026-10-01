from html import escape
from math import isfinite
import re

def format_phone(value):
    text = str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    parts = re.split(r"[|,؛\n]", text)
    formatted = []
    for part in parts:
        digits = re.sub(r"\D", "", part)
        if digits:
            formatted.append(digits)
    return "\n\n".join(formatted)


def neshan_url(latitude, longitude):
    try:
        lat, lng = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if not isfinite(lat) or not isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        return None
    return f"https://neshan.org/maps/routing/car/destination/{lat:.6f},{lng:.6f}"


def branch_details(name, address=None, phone=None, latitude=None, longitude=None, stock=None):
    sections = [f"🏢 <b>{escape(str(name))}</b>"]
    if stock is not None:
        sections.append(f"📦 {escape(str(stock))}")
    if phone:
        sections.append(f"📞 تلفن:\n{format_phone(phone)}")
    address_text = f"📍 نشانی: {escape(str(address or 'ثبت نشده'))}"
    from bot.services.branch_service import BranchService, RHINOTECH_BRANCHES

    branch_keys = BranchService.normalize_branch_string(str(name))
    shared_url = RHINOTECH_BRANCHES[branch_keys[0]].map_url if len(branch_keys) == 1 else None
    url = shared_url if shared_url and shared_url.startswith('https://nshn.ir/') else neshan_url(latitude, longitude)
    if url:
        label = RHINOTECH_BRANCHES[branch_keys[0]].map_label if len(branch_keys) == 1 else '🧭 مسیریابی در نشان'
        address_text += f'\n<a href="{url}">{escape(label)}</a>'
    sections.append(address_text)
    return "\n\n".join(sections)
