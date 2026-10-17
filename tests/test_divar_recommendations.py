from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from bot.services.divar_recommendations import RecommendationStore, PERIOD


def product(n):
    return dict(id=n,brand='Dell',model=f'Test {n}',cpu='i5',ram='8GB',storage='256GB',
                gpu='Intel',condition='در حد نو',price=n*100000,status='active',available=2,
                branches=[dict(branch_id=1,available=2,name='میرداماد',address='میرداماد')])


class Recommendations(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=RecommendationStore(Path(self.tmp.name)/'ads.db')
        self.staff=[dict(telegram_id=n,managed_branch_id=1) for n in (11,22,33)]
        self.products=[product(n) for n in range(1,8)]
        self.now=PERIOD*100+10

    def allocate(self,**kwargs):
        self.store.allocate(now=self.now,staff=self.staff,products=self.products,**kwargs)

    def test_unique_restart_and_rotation(self):
        self.allocate()
        rows=[self.store.current(u['telegram_id'],now=self.now) for u in self.staff]
        self.assertEqual(len({r['product_id'] for r in rows}),3)
        self.allocate()
        again=RecommendationStore(self.store.path)
        self.assertEqual(rows[0]['id'],again.current(11,now=self.now)['id'])
        self.store.allocate(now=self.now+PERIOD,staff=self.staff,products=self.products)
        for row in rows:
            self.assertNotEqual(row['product_id'],self.store.current(row['owner'],now=self.now+PERIOD)['product_id'])

    def test_edits_persist_refresh_and_invalidate_old_transfer(self):
        self.allocate()
        row=self.store.current(11,now=self.now)
        with patch.object(self.store,'staff',return_value=self.staff),patch('bot.services.divar_recommendations.inventory',return_value=self.products),patch('bot.services.divar_recommendations.time.time',return_value=self.now):
            with self.assertRaises(ValueError):
                self.store.edit_text(row['id'],22,row['digest'][:16],'Other title','Other description')
            self.store.edit_text(row['id'],11,row['digest'][:16],'Edited title','Edited description')
            with self.assertRaises(ValueError):
                self.store.review_transfer(row['id'],11,row['digest'][:16])
        self.allocate()
        edited=self.store.current(11,now=self.now)
        self.assertEqual(edited['ad']['title'],'Edited title')
        self.assertEqual(edited['ad']['description'],'Edited description')
        self.assertNotEqual(edited['digest'],row['digest'])
        changed=[p|{'price':123456} for p in self.products]
        self.store.allocate(now=self.now,staff=self.staff,products=changed)
        self.assertNotIn('custom_text',self.store.current(11,now=self.now)['ad'])

    def test_scarcity_never_duplicates_and_branch_scoped(self):
        self.store.allocate(now=self.now,staff=self.staff,products=self.products[:1])
        self.assertEqual(sum(self.store.current(u['telegram_id'],now=self.now) is not None for u in self.staff),1)
        self.store.allocate(now=self.now,staff=[dict(telegram_id=99,managed_branch_id=2)],products=self.products)
        self.assertIsNone(self.store.current(99,now=self.now))

    def test_refresh_replaces_changed_price_and_withdraws_sold_product(self):
        self.allocate()
        row=self.store.current(11,now=self.now)
        changed=[p|{'price':123456} for p in self.products]
        self.store.allocate(now=self.now,staff=self.staff,products=changed)
        fresh=self.store.current(11,now=self.now)
        self.assertEqual(fresh['id'],row['id'])
        self.assertNotEqual(fresh['digest'],row['digest'])
        self.assertEqual(fresh['ad']['price_toman'],123456)
        self.store.allocate(now=self.now,staff=self.staff,products=[])
        self.assertEqual(self.store.current(11,now=self.now)['state'],'unavailable')

    def test_owner_expiry_revocation_stock_change_and_idempotency(self):
        self.allocate()
        row=self.store.current(11,now=self.now)
        with patch.object(self.store,'staff',return_value=self.staff),patch('bot.services.divar_recommendations.inventory',return_value=self.products):
            for owner,now,digest in [(22,self.now,row['digest'][:16]),(11,self.now+PERIOD,row['digest'][:16]),(11,self.now,'bad')]:
                with self.assertRaises(ValueError):
                    self.store.review_transfer(row['id'],owner,digest,now=now)
            for _ in range(2):
                self.assertEqual(self.store.review_transfer(row['id'],11,row['digest'][:16],now=self.now),'blocked_account_connection')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM personal_ad_events').fetchone()[0],1)
        with patch.object(self.store,'staff',return_value=[]),patch('bot.services.divar_recommendations.inventory',return_value=self.products):
            with self.assertRaises(ValueError):
                self.store.review_transfer(row['id'],11,row['digest'][:16],now=self.now)
        changed=[p|{'price':1} for p in self.products]
        with patch.object(self.store,'staff',return_value=self.staff),patch('bot.services.divar_recommendations.inventory',return_value=changed):
            with self.assertRaises(ValueError):
                self.store.review_transfer(row['id'],11,row['digest'][:16],now=self.now)

    def test_link_binding_is_per_owner_and_never_proves_account_access(self):
        with patch.object(self.store,'staff',return_value=self.staff):
            url=self.store.register_link(11,'https://divar.ir/pro/StoreOne/panel/posts')
            self.store.register_link(22,'https://divar.ir/my-divar/my-business/StoreTwo')
            self.assertTrue(url.endswith('/StoreOne'))
            self.assertEqual(self.store.account_link(11)['verification'],'link_only')
            self.assertEqual(self.store.account_link(22)['business_id'],'StoreTwo')
            self.assertIsNone(self.store.account_link(33))
            restarted=RecommendationStore(self.store.path)
            self.assertEqual(restarted.account_link(11)['url'],url)
            for url in ('http://divar.ir/pro/StoreOne','https://divar.ir.evil.test/pro/StoreOne',
                        'https://divar.ir@evil.test/pro/StoreOne','https://divar.ir/v/abcdefgh',
                        'https://divar.ir/pro/StoreOne?token=secret'):
                with self.assertRaises(ValueError):
                    self.store.register_link(11,url)
            with self.assertRaises(ValueError):
                self.store.register_link(999,'https://divar.ir/pro/StoreOne')


class StoreCopyTests(TestCase):
    def test_store_ad_has_promotional_title_and_stock_as_example(self):
        from bot.services.divar_recommendations import build_ad
        p=product(7)
        ad=build_ad(p,p['branches'][0])
        self.assertNotIn(p['model'],ad['title'])
        self.assertIn('گارانتی',ad['title'])
        self.assertIn('نمونه موجود:',ad['description'])
        self.assertIn('700,000',ad['description'])
        self.assertIn('معاوضه',ad['description'])
        self.assertEqual(ad['price_toman'],p['price'])


class AdminStoreTests(TestCase):
    def test_allowlisted_admin_can_register_without_seller_role(self):
        import sqlite3
        from types import SimpleNamespace
        with TemporaryDirectory() as directory:
            db=Path(directory)/'inventory.db'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE users(telegram_id INTEGER, managed_branch_id INTEGER,role TEXT,is_active INTEGER)')
                c.executemany('INSERT INTO users VALUES(?,?,?,?)',[(99,None,'admin',1),(88,None,'admin',1),(77,None,'customer',1),(66,None,'admin',0)])
            c.close()
            store=RecommendationStore(Path(directory)/'ads.db',inventory_db=db)
            with patch('bot.config.get_settings',return_value=SimpleNamespace(ADMIN_TELEGRAM_IDS=[99,66])):
                self.assertEqual([u['telegram_id'] for u in store.staff()],[99])
                store.register_link(99,'https://divar.ir/pro/StoreAdmin')
                for owner in (88,77,66):
                    with self.assertRaises(ValueError):store.register_link(owner,'https://divar.ir/pro/StoreAdmin')
                people=store.staff()
                p=product(1)
                store.allocate(now=PERIOD*100+10,staff=people,products=[p])
                row=store.current(99,now=PERIOD*100+10)
                self.assertIsNotNone(row)
                with patch('bot.services.divar_recommendations.inventory',return_value=[p]):
                    store.review_transfer(row['id'],99,row['digest'][:16],now=PERIOD*100+10)


class BatchTests(TestCase):
    def test_ten_batches_unique_and_last_batch_transfer_valid(self):
        with TemporaryDirectory() as directory:
            store=RecommendationStore(Path(directory)/'ads.db')
            staff=[dict(telegram_id=11,managed_branch_id=1)]
            products=[product(i) for i in range(1,101)]
            now=PERIOD*100+10
            seen=set()
            for batch in range(10):
                store.allocate_daily(now=now,staff=staff,products=products,batch=batch)
                rows=store.daily_rows(11,now=now,batch=batch)
                self.assertEqual(len(rows),10)
                ids={r['product_id'] for r in rows}
                self.assertFalse(seen & ids)
                seen.update(ids)
            self.assertEqual(len(seen),100)
            row=rows[0]
            with patch.object(store,'staff',return_value=staff),patch('bot.services.divar_recommendations.inventory',return_value=products):
                store.review_transfer(row['id'],11,row['digest'][:16],now=now)
