"""Exact model photography, kept separate from source inventory and stock transactions."""
from functools import lru_cache
import json
from pathlib import Path
import re

ASSETS_DIR = Path(__file__).resolve().parent.parent / 'assets/model_photos'
MANIFEST_PATH = ASSETS_DIR / 'manifest.json'


def normalize(value):
    return re.sub(r'[^a-z0-9]', '', (value or '').casefold())


@lru_cache(maxsize=1)
def load_manifest():
    try:
        data=json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
        return data.get('photos', []) if isinstance(data,dict) else []
    except (OSError, ValueError):
        return []


def resolve_model_photo(brand, model, *, cpu=None, screen=None):
    matches=[]
    for entry in load_manifest():
        if entry.get('status') != 'reviewed':
            continue
        for identity in entry.get('identities', []):
            if normalize(identity.get('brand')) != normalize(brand) or normalize(identity.get('model')) != normalize(model):
                continue
            if cpu is not None and normalize(identity.get('cpu')) != normalize(cpu):
                continue
            if screen is not None and normalize(identity.get('screen')) != normalize(screen):
                continue
            filename=entry.get('file','')
            if not filename or Path(filename).name!=filename or not filename.endswith('.jpg'):
                continue
            path=(ASSETS_DIR / filename).resolve()
            if not path.is_relative_to(ASSETS_DIR.resolve()) or not path.is_file():
                continue
            matches.append(path)
            break
    unique=set(matches)
    # Unspecified generations/screen sizes never choose an arbitrary photograph.
    return next(iter(unique)) if len(unique)==1 else None
