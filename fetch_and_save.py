#!/usr/bin/env python3
"""v10 - Playwright + Cookie screenshot dashboard"""
import os, sys, traceback, logging
from datetime import datetime, timezone, timedelta
import requests
from playwright.sync_api import sync_playwright

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ["BITABLE_TABLE_ID"]
FEISHU_COOKIE = os.environ.get("FEISHU_COOKIE", "")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://mi.feishu.cn/share/base/dashboard/shrcnfII9800SafQCLkKZmHMmUf")
ATTACHMENT_FIELD = os.environ.get("ATTACHMENT_FIELD", "报告")
FILE_NAME_PREFIX = os.environ.get("FILE_NAME_PREFIX", "试驾安全日报")
BASE_URL = "https://open.feishu.cn"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

def get_token():
    r = requests.post(f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    d = r.json()
    if d.get("code") != 0: raise RuntimeError(f"Token fail: {d}")
    log.info("OK token")
    return d["tenant_access_token"]

def parse_cookies(s, domain):
    cs = []
    for i in s.split("; "):
        if "=" not in i: continue
        n, v = i.split("=", 1)
        cs.append({"name": n.strip(), "value": v.strip(), "domain": domain, "path": "/"})
    log.info(f"OK {len(cs)} cookies -> {domain}")
    return cs

def screenshot(url):
    if not FEISHU_COOKIE: raise RuntimeError("FEISHU_COOKIE not set")
    log.info(f"Opening {url}")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        ctx = b.new_context(viewport={"width":1920,"height":1080}, locale="zh-CN")
        cs = parse_cookies(FEISHU_COOKIE, ".feishu.cn") + parse_cookies(FEISHU_COOKIE, ".mi.feishu.cn")
        ctx.add_cookies(cs)
        log.info("OK cookies injected")
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        log.info("Page loaded")
        for _ in range(5):
            for s in ['button:has-text("OK")','button:has-text("Close")','[class*="close"]']:
                try:
                    btn = pg.locator(s)
                    if btn.count() > 0: btn.first.click(timeout=2000)
                except: pass
            pg.wait_for_timeout(1000)
        for s in ["canvas","svg","img","[class*='chart']"]:
            try: pg.wait_for_selector(s, timeout=10000); log.info(f"Found {s}"); break
            except: continue
        pg.wait_for_timeout(8000)
        pg.evaluate("window.scrollTo(0,document.body.scrollHeight)")
        pg.wait_for_timeout(2000)
        pg.evaluate("window.scrollTo(0,0)")
        pg.wait_for_timeout(1000)
        shot = pg.screenshot(full_page=True, type="png")
        log.info(f"OK screenshot {len(shot)} bytes")
        b.close()
    return shot

def upload(token, data, name):
    r = requests.post(f"{BASE_URL}/open-apis/drive/v1/medias/upload_all",
        headers={"Authorization":f"Bearer {token}"},
        data={"file_name":(None,name),"parent_type":(None,"bitable_image"),"parent_node":(None,BITABLE_APP_TOKEN),"size":(None,str(len(data)))},
        files={"file":(name,data,"image/png")}, timeout=120)
    d = r.json()
    if d.get("code") != 0: raise RuntimeError(f"Upload fail: {d}")
    log.info(f"OK upload {d['data']['file_token']}")
    return d["data"]["file_token"]

def create_record(token, ft):
    now = datetime.now(timezone(timedelta(hours=8)))
    fs = {"日期": int(now.timestamp()*1000), "报告类型": "日报", ATTACHMENT_FIELD: [{"file_token": ft}]}
    r = requests.post(f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records",
        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
        json={"fields":fs}, timeout=15)
    d = r.json()
    if d.get("code") != 0: raise RuntimeError(f"Record fail: {d}")
    log.info(f"OK record {d['data']['record']['record_id']}")
    return d["data"]["record"]["record_id"]

def main():
    log.info("=== v10 start ===")
    token = get_token()
    data = screenshot(DASHBOARD_URL)
    now = datetime.now(timezone(timedelta(hours=8)))
    fn = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"
    ft = upload(token, data, fn)
    rid = create_record(token, ft)
    log.info(f"DONE rid={rid}")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        log.error(f"FAIL: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)