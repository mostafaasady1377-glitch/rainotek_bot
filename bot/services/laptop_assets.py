from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from aiogram.types import FSInputFile, URLInputFile
from loguru import logger
from bot.services.model_photos import resolve_model_photo, load_manifest, ASSETS_DIR as MODEL_ASSETS_DIR, normalize
import json

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "laptops"

LAPTOP_IMAGE_SOURCES = {
    "apple_macbook_pro": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=800&q=80",
    "apple_macbook_air": "https://images.unsplash.com/photo-1611186871348-b1ce696e52c9?w=800&q=80",
    "apple_iphone": "https://images.unsplash.com/photo-1592750475338-74b7b21085ab?w=800&q=80",
    "asus_tuf": "https://images.unsplash.com/photo-1603302576837-37561b2e2302?w=800&q=80",
    "asus_rog": "https://images.unsplash.com/photo-1593642702821-c8da6771f0c6?w=800&q=80",
    "asus_zenbook": "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=800&q=80",
    "asus_vivobook": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800&q=80",
    "acer_nitro": "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=800&q=80",
    "acer_predator": "https://images.unsplash.com/photo-1593642634367-d91a135587b5?w=800&q=80",
    "acer_aspire": "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?w=800&q=80",
    "dell_latitude": "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=800&q=80",
    "dell_inspiron": "https://images.unsplash.com/photo-1593642532400-2682810df593?w=800&q=80",
    "hp_victus": "https://images.unsplash.com/photo-1603302576837-37561b2e2302?w=800&q=80",
    "hp_omen": "https://images.unsplash.com/photo-1593642702821-c8da6771f0c6?w=800&q=80",
    "hp_elitebook": "https://images.unsplash.com/photo-1589561084283-930aa7b1ce50?w=800&q=80",
    "hp_probook": "https://images.unsplash.com/photo-1541807084-5c52b6b3adef?w=800&q=80",
    "hp_zbook": "https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=800&q=80",
    "lenovo_thinkpad": "https://images.unsplash.com/photo-1541807084-5c52b6b3adef?w=800&q=80",
    "lenovo_legion": "https://images.unsplash.com/photo-1603302576837-37561b2e2302?w=800&q=80",
    "lenovo_ideapad": "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?w=800&q=80",
    "microsoft_surface_pro": "https://images.unsplash.com/photo-1527864550417-7fd91fc51a46?w=800&q=80",
    "microsoft_surface_book": "https://images.unsplash.com/photo-1544716278-ca5e3f4abd8c?w=800&q=80",
    "msi_gaming": "https://images.unsplash.com/photo-1593642702821-c8da6771f0c6?w=800&q=80",
    "sony_vaio": "https://images.unsplash.com/photo-1541807084-5c52b6b3adef?w=800&q=80",
    "toshiba_dynabook": "https://images.unsplash.com/photo-1589561084283-930aa7b1ce50?w=800&q=80",
    "fujitsu_lifebook": "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?w=800&q=80",
    "case_pc": "https://images.unsplash.com/photo-1587202372775-e229f172b9d7?w=800&q=80",
    "all_in_one": "https://images.unsplash.com/photo-1547082299-de196ea013d6?w=800&q=80",
    "monitor": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=800&q=80",
    "generic_laptop": "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=800&q=80",
}


def resolve_image_key(brand: str, model: str) -> str:
    b = (brand or "").upper()
    m = (model or "").upper()

    if "APPLE" in b or "IPHONE" in b:
        if "PRO" in m:
            return "apple_macbook_pro"
        elif "AIR" in m:
            return "apple_macbook_air"
        elif "IPHONE" in b or "IPHONE" in m:
            return "apple_iphone"
        return "apple_macbook_pro"

    if "ASUS" in b:
        if "TUF" in m:
            return "asus_tuf"
        elif "ROG" in m:
            return "asus_rog"
        elif "ZENBOOK" in m:
            return "asus_zenbook"
        elif "VIVO" in m:
            return "asus_vivobook"
        return "asus_vivobook"

    if "ACER" in b:
        if "NITRO" in m:
            return "acer_nitro"
        elif "PREDATOR" in m:
            return "acer_predator"
        return "acer_aspire"

    if "DELL" in b:
        if "LATITUDE" in m or any(num in m for num in ["5420", "5520", "5430", "6430", "3570"]):
            return "dell_latitude"
        return "dell_inspiron"

    if "HP" in b:
        if "VICTUS" in m:
            return "hp_victus"
        elif "OMEN" in m:
            return "hp_omen"
        elif "ELITEBOOK" in m or "ELITE" in m:
            return "hp_elitebook"
        elif "PROBOOK" in m:
            return "hp_probook"
        elif "ZBOOK" in m:
            return "hp_zbook"
        elif "CASE" in m:
            return "case_pc"
        return "hp_elitebook"

    if "LENOVO" in b:
        if "LEGION" in m:
            return "lenovo_legion"
        elif "THINKPAD" in m or "THINK" in m or "T4" in m or "X1" in m:
            return "lenovo_thinkpad"
        return "lenovo_ideapad"

    if "MICROSOFT" in b or "SURFACE" in m:
        if "BOOK" in m:
            return "microsoft_surface_book"
        return "microsoft_surface_pro"

    if "MSI" in b:
        return "msi_gaming"
    if "SONY" in b or "VAIO" in m:
        return "sony_vaio"
    if "TOSHIBA" in b or "DYNABOOK" in m:
        return "toshiba_dynabook"
    if "FUJITSU" in b:
        return "fujitsu_lifebook"
    if "CASE" in b:
        return "case_pc"
    if "ALL IN ONE" in b:
        return "all_in_one"
    if "NEC" in b:
        return "fujitsu_lifebook"
    if "SAMSUNG" in b or "GALAXY" in m:
        return "asus_zenbook"
    if "HUAWEI" in b or "MATEBOOK" in m:
        return "asus_zenbook"
    if "RAZER" in b:
        return "asus_rog"

    return "generic_laptop"


def get_laptop_image_source(brand: str, model: str) -> tuple[str, str, Optional[Path]]:
    """
    برگرداندن (key, remote_url, local_file_path_if_exists)
    """
    key = resolve_image_key(brand, model)
    remote_url = LAPTOP_IMAGE_SOURCES.get(key, LAPTOP_IMAGE_SOURCES["generic_laptop"])
    local_file = ASSETS_DIR / f"{key}.jpg"
    if local_file.exists() and local_file.stat().st_size > 1000:
        return key, remote_url, local_file
    generic_local = ASSETS_DIR / "generic_laptop.jpg"
    if generic_local.exists() and generic_local.stat().st_size > 1000:
        return key, remote_url, generic_local
    return key, remote_url, None


def get_laptop_photo_input(brand: str, model: str, custom_url: Optional[str] = None, *, cpu: Optional[str] = None, screen: Optional[str] = None) -> FSInputFile | URLInputFile | None:
    local_file = resolve_model_photo(brand, model, cpu=cpu, screen=screen)
    if local_file:
        return FSInputFile(local_file)
    if custom_url and custom_url.startswith(("http://", "https://")) and "images.unsplash.com" not in custom_url:
        return URLInputFile(custom_url)
    return None


def get_brand_photo_input(brand):
    for entry in load_manifest():
        if entry.get('status') == 'reviewed' and any(normalize(identity.get('brand')) == normalize(brand) for identity in entry.get('identities', [])):
            name=entry.get('file','')
            if Path(name).name == name and (MODEL_ASSETS_DIR/name).is_file():
                return FSInputFile(MODEL_ASSETS_DIR/name)
    folder=ASSETS_DIR.parent/'brand_photos'
    try:
        entries=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        entry=entries.get(brand,{})
        name=entry.get('file','')
        if entry.get('kind') == 'sample_laptop' and name and Path(name).name==name and (folder/name).is_file():
            return FSInputFile(folder/name)
    except (OSError,ValueError):
        pass
    # These are merchandise categories, not manufacturers.
    if brand in ('Monitor & All in One','Case & Mini PC'):
        key='monitor' if brand=='Monitor & All in One' else 'case_pc'
        local=ASSETS_DIR/(key+'.jpg')
        if local.is_file():
            return FSInputFile(local)
    return None
