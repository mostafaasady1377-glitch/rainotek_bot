from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import hashlib
import json
from bot.services.divar_gallery import gallery_files


class GalleryTests(TestCase):
    def test_actual_gallery_order_hashes_and_variants(self):
        for n in range(10):
            photos=gallery_files(n)
            self.assertEqual(len(photos),15)
            self.assertEqual([p['kind'] for p in photos],['shop']*2+['single_laptop']*13)
            self.assertEqual(len({p['sha256'] for p in photos}),15)
        self.assertNotEqual([p['sha256'] for p in gallery_files(0)],[p['sha256'] for p in gallery_files(1)])

    def test_missing_corrupt_and_escaping_assets_rejected(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            entries=[]
            for i in range(15):
                data=bytes([i]);name=f'{i}.jpg';(root/name).write_bytes(data)
                entries.append({'file':name,'sha256':hashlib.sha256(data).hexdigest()})
            (root/'manifest.json').write_text(json.dumps({'layout':'2_shop_13_single','sets':[entries]}))
            gallery_files(0,root)
            (root/'0.jpg').write_bytes(b'changed')
            with self.assertRaises(ValueError):gallery_files(0,root)
