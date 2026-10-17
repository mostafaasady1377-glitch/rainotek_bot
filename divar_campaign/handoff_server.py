"""Run behind HTTPS ingress: python -m divar_campaign.handoff_server. Never a publisher."""
import asyncio
import os
from aiohttp import web
from bot.services.divar_handoff import HandoffStore


def create_app(store=None):
    app=web.Application(client_max_size=2048)
    app['store']=store or HandoffStore()

    async def redeem(request):
        header=request.headers.get('Authorization','')
        if not header.startswith('Bearer '):
            return web.json_response({'error':'ticket_required'},status=401)
        try:
            payload=await asyncio.to_thread(app['store'].redeem,header[7:])
        except ValueError:
            return web.json_response({'error':'invalid_expired_or_changed'},status=409,headers={'Cache-Control':'no-store'})
        return web.json_response(payload,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    async def health(request):
        return web.json_response({'service':'rainotek-browser-handoff','posts_to_divar':False})

    async def index(request):
        return web.Response(text='<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8"><title>اتصال محلی راینوتک</title><body style="font-family:Tahoma;max-width:700px;margin:60px auto;line-height:2;padding:20px"><h1>سرویس محلی راینوتک فعال است</h1><p>این صفحه تأیید دریافت آگهی یا نصب افزونه نیست.</p><ol><li>در Google Chrome، روی علامت افزونه\u200cها (🧩) بزنید.</li><li>RAINOTEK Divar Handoff را باز کنید.</li><li>آدرس <code dir="ltr">http://127.0.0.1:8769</code> را در کادر افزونه ثبت کنید و اجازه اتصال را بدهید.</li><li>در بات، آگهی آماده را تأیید و لینک انتقال را با همین Chrome باز کنید.</li></ol><p>ورود دیوار در مرورگر خودتان انجام می\u200cشود. ثبت نهایی و پرداخت با شماست.</p><p>این سرویس فقط از همین کامپیوتر قابل استفاده است.</p><a href="/health">بررسی سلامت سرویس</a></body></html>',content_type='text/html',headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'"})

    app.router.add_get('/',index)
    app.router.add_post('/v1/redeem',redeem)
    app.router.add_get('/health',health)
    return app


if __name__=='__main__':
    web.run_app(create_app(),host=os.environ.get('DIVAR_HANDOFF_HOST','127.0.0.1'),port=int(os.environ.get('DIVAR_HANDOFF_PORT',os.environ.get('PORT','8769'))),access_log=None)
