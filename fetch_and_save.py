#!/usr/bin/env python3
"""
飞书仪表盘自动截图存档 v7
- Playwright + Cookie 注入，无需登录
"""

import os
import sys
import traceback
import logging
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ["BITABLE_TABLE_ID"]
FEISHU_COOKIE = os.environ.get("FEISHU_COOKIE", "")

DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://mi.feishu.cn/share/base/dashboard/shrcnfII9800SafQCLkKZmHMmUf"
)

ATTACHMENT_FIELD = os.environ.get("ATTACHMENT_FIELD", "报告")
FILE_NAME_PREFIX = os.environ.get("FILE_NAME_PREFIX", "试驾安全日报")

BASE_URL = "https://open.feishu.cn"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_tenant_token() -> str:
    url = f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    log.info("✅ tenant_access_token 获取成功")
    return data["tenant_access_token"]


def parse_cookies(cookie_str: str, domain: str) -> list[dict]:
    """将 cookie 字符串解析为 Playwright cookie 格式"""
    cookies = []
    for item in cookie_str.split("; "):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": domain,
            "path": "/",
        })
    log.info(f"解析了 {len(cookies)} 个 cookie")
    return cookies


def screenshot_dashboard(url: str) -> bytes:
    """用 Playwright + Cookie 截图仪表盘"""
    log.info(f"打开仪表盘: {url}")

    if not FEISHU_COOKIE:
        raise RuntimeError("FEISHU_COOKIE 未配置！请在 GitHub Secrets 中添加")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
        )

        # 注入 Cookie
        cookies = parse_cookies(FEISHU_COOKIE, ".feishu.cn")
        context.add_cookies(cookies)
        log.info("✅ Cookie 已注入")

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        log.info("页面已加载，等待渲染...")

        # 处理可能的弹窗
        for _ in range(5):
            try:
                btns = page.locator('button:has-text("我知道了"), button:has-text("关闭"), button:has-text("确定"), [class*="close"], [aria-label="关闭"]')
                if btns.count() > 0:
                    btns.first.click(timeout=2000)
                    log.info("关闭了弹窗")
            except Exception:
                pass
            page.wait_for_timeout(1000)

        # 等待图表渲染
        log.info("等待图表渲染...")
        for selector in ["canvas", "svg", "img", "[class*='chart']", "[class*='Chart']", "[class*='dashboard']"]:
            try:
                page.wait_for_selector(selector, timeout=10000)
                log.info(f"检测到: {selector}")
                break
            except Exception:
                continue

        # 等待异步数据加载
        page.wait_for_timeout(8000)

        # 滚动触发懒加载
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(1000)

        screenshot = page.screenshot(full_page=True, type="png")
        log.info(f"✅ 截图成功: {len(screenshot)} bytes")

        browser.close()

    return screenshot


def upload_to_drive(token: str, image_data: bytes, filename: str) -> str:
    url = f"{BASE_URL}/open-apis/drive/v1/medias/upload_all"
    headers = {"Authorization": f"Bearer {token}"}
    form_data = {
        "file_name": (None, filename),
        "parent_type": (None, "bitable_image"),
        "parent_node": (None, BITABLE_APP_TOKEN),
        "size": (None, str(len(image_data))),
    }
    files = {"file": (filename, image_data, "image/png")}
    resp = requests.post(url, headers=headers, data=form_data, files=files, timeout=120)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"上传云盘失败: {data}")
    ft = data["data"]["file_token"]
    log.info(f"✅ 上传成功: file_token={ft}")
    return ft


def create_bitable_record(token: str, file_token: str) -> str:
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    now = datetime.now(timezone(timedelta(hours=8)))
    ts_ms = int(now.timestamp() * 1000)

    fields = {
        "日期": ts_ms,
        "报告类型": "日报",
        ATTACHMENT_FIELD: [{"file_token": file_token}],
    }
    resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建记录失败: {data}")
    rid = data["data"]["record"]["record_id"]
    log.info(f"✅ 记录创建成功: record_id={rid}")
    return rid


def main():
    log.info("=" * 50)
    log.info("飞书仪表盘自动截图存档 v7")
    log.info(f"  DASHBOARD_URL={DASHBOARD_URL}")
    log.info(f"  COOKIE={'已配置' if FEISHU_COOKIE else '❌ 未配置'}")
    log.info("=" * 50)

    screenshot_data = screenshot_dashboard(DASHBOARD_URL)

    now = datetime.now(timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"

    token = get_tenant_token()
    file_token = upload_to_drive(token, screenshot_data, filename)
    record_id = create_bitable_record(token, file_token)
    log.info(f"🎉 完成！record_id={record_id}, file={filename}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"❌ 运行失败: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)