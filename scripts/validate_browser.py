"""Headless real-browser checks against an isolated, explicitly synthetic database."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import logging
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory',type=Path,default=ROOT/'tmp/browser-validation')
    args=parser.parse_args();output=args.output_directory.resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='isolated-browser-',dir=output) as temporary:
        private=Path(temporary).resolve()
        if not private.is_relative_to(output):raise ValueError('Invalid temporary workspace')
        password=secrets.token_urlsafe(20)
        env={**os.environ,'ENVIRONMENT':'development','AUTH_SECRET':secrets.token_hex(32),
            'DATABASE_URL':'sqlite+pysqlite:///'+(private/'browser.db').as_posix(),
            'SECURE_UPLOAD_DIR':str(private/'uploads'),'UPLOAD_STORAGE_BACKEND':'local',
            'MALWARE_SCAN_REQUIRED':'false','CLAMAV_HOST':'','OIDC_ENABLED':'false',
            'LOCAL_AUTH_ENABLED':'true','DEMO_AUTH_ENABLED':'false','REDIS_URL':'',
            'OTP_EMAIL_BACKEND':'terminal','OTP_SMS_BACKEND':'terminal','RATE_LIMIT_REQUESTS':'10000',
            'PORTAL_SEED_PASSWORD':password}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        def command(*parts):
            result=subprocess.run([sys.executable,*parts],cwd=ROOT,env=env,capture_output=True,text=True)
            if result.returncode:raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
        command('-m','scripts.migrate')
        command('-m','scripts.seed_tamil_nadu_fra_demo')
        with (output/'server.log').open('w',encoding='utf-8') as log:
            # A managed thread avoids leaving a child behind the Windows venv launcher.
            os.environ.update(env)
            logging.basicConfig(stream=log,level=logging.INFO,force=True)
            import uvicorn
            server=uvicorn.Server(uvicorn.Config('app.main:app',host='127.0.0.1',port=port,log_config=None))
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            try:
                for _ in range(60):
                    if not thread.is_alive():raise RuntimeError('Isolated server exited; inspect server.log')
                    try:
                        with urlopen(base+'/health/ready',timeout=1) as response:
                            if response.status==200:break
                    except Exception:time.sleep(.25)
                else:raise RuntimeError('Isolated server did not become ready')
                command('-m','scripts.seed_community_portal','--confirm-synthetic','--base-url',base)
                errors=[];checks=[]
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    user=browser.new_context(viewport={'width':1440,'height':1000})
                    staff=browser.new_context(viewport={'width':1440,'height':1000})
                    for context in [user,staff]:
                        # Keep font-provider availability out of application correctness checks.
                        context.route('https://fonts.googleapis.com/**',lambda route:route.abort())
                        context.route('https://fonts.gstatic.com/**',lambda route:route.abort())
                    def page_for(context):
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.on('dialog',lambda dialog:dialog.accept())
                        return page
                    def login(page,username):
                        page.goto(base+'/login',wait_until='domcontentloaded')
                        page.locator('#username').fill(username);page.locator('#password').fill(password)
                        page.locator('#loginButton').click();page.wait_for_url('**/'+('fra' if username.endswith('reviewer') else 'my-plots'))
                    page=page_for(user);login(page,'synthetic-portal-alice')
                    page.locator('#dashboardMetrics').wait_for();page.wait_for_function("document.querySelector('#dashboardMetrics').children.length > 0")
                    page.screenshot(path=output/'user-dashboard.png',full_page=True);checks.append('real browser account login and database-backed dashboard')
                    page.goto(base+'/my-plots#map',wait_until='domcontentloaded')
                    page.wait_for_function('window.UserPlotMap && typeof L !== "undefined"')
                    page.locator('#latitude').fill('11.66');page.locator('#longitude').fill('78.14');page.locator('#coordinateSearch button').click()
                    page.wait_for_timeout(400)
                    rectangle=page.locator('#plotMap').bounding_box()
                    for x,y in [(0.4,0.4),(0.6,0.4),(0.6,0.6),(0.4,0.6)]:
                        page.mouse.click(rectangle['x']+rectangle['width']*x,rectangle['y']+rectangle['height']*y)
                    page.locator('#closeBoundary').click();page.locator('#plotName').fill('Synthetic browser plot');page.locator('#plotVillage').fill('Synthetic locality')
                    page.locator('#savePlotForm button[type=submit]').click()
                    page.wait_for_function('window.UserPlotMap.getDraft().saved !== null')
                    page.locator('#submitPlot').click();page.wait_for_function("document.querySelector('#submissionState').textContent.includes('submitted')")
                    checks.append('draw, close, save and submit a boundary through browser controls')
                    officer=page_for(staff);login(officer,'synthetic-portal-reviewer')
                    officer.goto(base+'/staff-services',wait_until='domcontentloaded')
                    officer.locator('#reviewList button').filter(has_text='Synthetic browser plot').click()
                    officer.locator('#reviewForm select').select_option('approved')
                    officer.locator('#reviewForm textarea').fill('SYNTHETIC browser test: reviewed the submitted boundary.')
                    officer.locator('#reviewForm button').click()
                    officer.wait_for_function("document.querySelector('#reviewMeta').textContent.includes('approved')")
                    officer.screenshot(path=output/'staff-review.png',full_page=True)
                    checks.append('independent staff session reviews the submitted boundary')
                    page.goto(base+'/my-plots#saved',wait_until='domcontentloaded')
                    card=page.locator('#plotWorkflowList article').filter(has_text='Synthetic browser plot')
                    card.get_by_role('button',name='Satellite land cover').click()
                    page.get_by_text('No analysis has been requested for this approved boundary.').wait_for()
                    page.screenshot(path=output/'satellite-dialog.png',full_page=True)
                    page.locator('.land-cover-dialog').get_by_role('button',name='Close',exact=True).click()
                    checks.append('approved plot exposes the real satellite-analysis dialog')
                    for section in ['atlas','assets','planner','reports']:
                        officer.goto(base+'/fra#'+section,wait_until='domcontentloaded')
                        officer.locator('#'+section+'Panel').wait_for()
                        officer.wait_for_timeout(400)
                        if officer.locator('#'+section+'Panel').get_by_text('Under development',exact=True).count():raise AssertionError('Workspace is still hidden')
                        officer.screenshot(path=output/(section+'.png'),full_page=True)
                        checks.append('rendered '+section+' workspace')
                    page.set_viewport_size({'width':390,'height':844});page.goto(base+'/my-plots#dashboard',wait_until='domcontentloaded')
                    page.evaluate('window.scrollTo(0,0)')
                    page.locator('#dashboardMetrics').wait_for();page.screenshot(path=output/'user-mobile.png',full_page=True)
                    checks.append('mobile dashboard rendered')
                    browser.close()
                report={'synthetic':True,'scope':'isolated browser execution, not a model benchmark',
                    'checks':checks,'javascript_errors':errors,'passed':not errors,
                    'font_provider_requests':'blocked; system fallback fonts used'}
                (output/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                if errors:raise RuntimeError('Browser JavaScript errors: '+str(errors))
                print(json.dumps(report,indent=2))
            finally:
                server.should_exit=True;thread.join(timeout=20)
                if thread.is_alive():raise RuntimeError('Isolated HTTP server did not stop cleanly.')
                from app.db.session import get_engine
                get_engine().dispose()


if __name__=='__main__':main()
