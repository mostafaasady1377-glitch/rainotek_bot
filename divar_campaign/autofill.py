"""Visible local browser handoff; never submits, pays, or uploads reference photos."""
import argparse
import json
from pathlib import Path

from bot.services.divar_campaigns import CampaignStore
from divar_campaign.engine import ROOT, RUNTIME, fingerprint, inventory

FORM_URL = 'https://divar.ir/new'


def fill_text_fields(page, ad):
    title=page.get_by_placeholder('عنوان آگهی خود را بنویسید',exact=True)
    description=page.get_by_placeholder('توضیحات مربوط به آگهی را بنویسید',exact=True)
    title.wait_for(state='visible',timeout=20000)
    # Preserve a human's in-progress draft instead of replacing it silently.
    for field,value in ((title,ad['title']),(description,ad['description'])):
        existing=field.input_value()
        if existing.strip() and existing!=value:
            raise ValueError('existing_draft_requires_review')
    title.fill(ad['title'])
    description.fill(ad['description'])
    if title.input_value()!=ad['title'] or description.input_value()!=ad['description']:
        raise ValueError('prefill_verification_failed')
    # Specs and price are present in the description; later structured pages need
    # a verified actual-unit photo and fresh UI schema before advancing.
    return {'title':True,'description_with_specs_and_price':True,'structured_later_pages':False,
            'photo_uploaded':False,'submitted':False,'paid':False}


def validated_ad(number):
    store=CampaignStore()
    store.sync()
    row=store.get(number)
    if not row or row['state']!='editorial_approved':
        raise ValueError('fresh_human_approval_required')
    ad=row['ad']
    product=next((p for p in inventory(store.inventory_db) if p['id']==ad['product_id']),None)
    if not product or fingerprint(product)!=ad['inventory_fingerprint']:
        raise ValueError('inventory_changed')
    return ad


def run(number):
    # OS lock survives bot restarts and prevents concurrent sessions using one profile.
    import msvcrt
    RUNTIME.mkdir(parents=True,exist_ok=True)
    with (RUNTIME/'autofill.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:
            _run(number)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


def _run(number):
    from playwright.sync_api import sync_playwright
    ad=validated_ad(number)
    status=RUNTIME/f'autofill-{number}.json'
    profile=RUNTIME/'autofill-chrome-profile'
    def record(state,**details):
        status.write_text(json.dumps({'number':number,'state':state,**details},ensure_ascii=False,indent=2),encoding='utf8')
    with sync_playwright() as p:
        context=p.chromium.launch_persistent_context(str(profile),
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            headless=False,no_viewport=True)
        page=context.pages[0] if context.pages else context.new_page()
        record('opening')
        try:
            page.goto(FORM_URL,wait_until='domcontentloaded',timeout=45000)
            record('waiting_for_form_or_user_login')
            # User completes login, OTP, CAPTCHA and chooses category if required.
            page.get_by_placeholder('عنوان آگهی خود را بنویسید',exact=True).wait_for(state='visible',timeout=180000)
            ad=validated_ad(number)
            result=fill_text_fields(page,ad)
            page.screenshot(path=str(RUNTIME/f'autofill-{number}.png'))
            record('text_fields_filled_human_review_required',**result)
        except Exception as exc:
            record('blocked',reason=type(exc).__name__)
        while context.pages:
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('number',type=int)
    args=parser.parse_args()
    run(args.number)
