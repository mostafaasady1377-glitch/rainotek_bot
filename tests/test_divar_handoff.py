import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from bot.services.divar_handoff import HandoffStore
from bot.services.divar_recommendations import PERIOD


class HandoffTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=HandoffStore(Path(self.tmp.name)/'queue.db')
        self.staff=[{'telegram_id':11,'managed_branch_id':1}]
        self.products=[dict(id=1,brand='Dell',model='Test',cpu='i5',ram='8GB',storage='256GB',gpu='Intel',condition='در حد نو',price=100000,status='active',available=1,branches=[dict(branch_id=1,available=1,name='شعبه',address='تهران')])]
        self.now=PERIOD*100+10
        self.store.allocate(now=self.now,staff=self.staff,products=self.products)
        self.row=self.store.current(11,now=self.now)
        for mock in (patch.object(self.store,'staff',return_value=self.staff),patch('bot.services.divar_recommendations.inventory',return_value=self.products)):
            mock.start();self.addCleanup(mock.stop)
        self.store.register_link(11,'https://divar.ir/pro/StoreOne')

    def issue(self,**kwargs):
        return self.store.issue(self.row['id'],11,self.row['digest'][:16],now=self.now,base_url='https://handoff.example.com',**kwargs)

    def ticket(self):
        url=self.issue()
        capsule=url.split('#rainotek=')[1]
        return json.loads(base64.urlsafe_b64decode(capsule+'='*(-len(capsule)%4)))['ticket']

    def test_one_use_exact_package_and_no_posting(self):
        ticket=self.ticket()
        with patch('bot.services.divar_handoff.ROOT',Path(self.tmp.name)):
            result=self.store.redeem(ticket,now=self.now+1)
        self.assertEqual(result['business_id'],'StoreOne')
        self.assertEqual(result['price_toman'],100000)
        self.assertEqual(len(result['photos']),15)
        self.assertEqual([p['kind'] for p in result['photos']],['shop']*2+['single_laptop']*13)
        self.assertFalse(result['submitted'])
        self.assertFalse(result['paid'])
        with self.assertRaises(ValueError):self.store.redeem(ticket,now=self.now+2)

    def test_expiry_and_account_change_fail_closed(self):
        ticket=self.ticket()
        with self.assertRaises(ValueError):self.store.redeem(ticket,now=self.now+901)
        self.store.register_link(11,'https://divar.ir/pro/StoreTwo')
        with self.assertRaises(ValueError):self.store.redeem(ticket,now=self.now+1)

    def test_no_endpoint_no_success_url(self):
        with self.assertRaises(ValueError):
            self.store.issue(self.row['id'],11,self.row['digest'][:16],base_url='http://example.com',now=self.now)

    def test_loopback_only_http_endpoint(self):
        url=self.store.issue(self.row['id'],11,self.row['digest'][:16],base_url='http://127.0.0.1:8769',now=self.now)
        self.assertIn('#rainotek=',url)
        for origin in ('http://192.168.1.5:8769','http://127.0.0.1:8000','http://127.0.0.1.evil.test:8769'):
            with self.assertRaises(ValueError):
                self.store.issue(self.row['id'],11,self.row['digest'][:16],base_url=origin,now=self.now)
