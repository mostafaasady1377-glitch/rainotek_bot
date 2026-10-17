"""Real loopback HTTP roundtrip; never contacts or posts to Divar."""
import asyncio
import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from aiohttp import ClientSession,web
from bot.services.divar_handoff import HandoffStore
from bot.services.divar_recommendations import PERIOD
from divar_campaign.handoff_server import create_app

class HandoffHttpTests(IsolatedAsyncioTestCase):
    async def test_actual_http_gallery_and_replay(self):
        with TemporaryDirectory() as directory:
            store=HandoffStore(Path(directory)/'handoff.db')
            staff=[{'telegram_id':11,'managed_branch_id':1}]
            products=[dict(id=1,brand='Dell',model='Test',cpu='i5',ram='8GB',storage='256GB',gpu='Intel',condition='در حد نو',price=100000,status='active',available=1,branches=[dict(branch_id=1,available=1,name='Test',address='Tehran')])]
            import time
            now=int(time.time())
            store.allocate(now=now,staff=staff,products=products)
            with patch.object(store,'staff',return_value=staff),patch('bot.services.divar_recommendations.inventory',return_value=products):
                store.register_link(11,'https://divar.ir/pro/StoreOne')
                row=store.current(11,now=now)
                url=store.issue(row['id'],11,row['digest'][:16],base_url='http://127.0.0.1:8769',now=now)
                capsule=url.split('#rainotek=')[1]
                descriptor=json.loads(base64.urlsafe_b64decode(capsule+'='*(-len(capsule)%4)))
                runner=web.AppRunner(create_app(store));await runner.setup()
                site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
                port=site._server.sockets[0].getsockname()[1]
                try:
                    async with ClientSession() as client:
                        endpoint=f'http://127.0.0.1:{port}/v1/redeem'
                        async with client.post(endpoint) as response:
                            self.assertEqual(response.status,401)
                        headers={'Authorization':'Bearer '+descriptor['ticket']}
                        async with client.post(endpoint,headers=headers) as response:
                            self.assertEqual(response.status,200)
                            self.assertEqual(response.headers['Cache-Control'],'no-store')
                            payload=await response.json()
                            self.assertEqual(payload['business_id'],'StoreOne')
                            self.assertEqual(len(payload['photos']),15)
                            self.assertFalse(payload['submitted'])
                            self.assertFalse(payload['paid'])
                        async with client.post(endpoint,headers=headers) as response:
                            self.assertEqual(response.status,409)
                finally:
                    await runner.cleanup()
