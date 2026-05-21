#!/usr/bin/env python3
"""
飞书仪表盘自动截图存档 v6
- 用 Playwright 无头浏览器直接截图仪表盘
- 上传到飞书云盘并写入 Bitable
- 不依赖卡片中的 image_key
"""

import os
import sys
import json
import traceback
import logging
from datetime import datetime, timezone, timedelta

import requests
from playwright.sync_api import sync_playwright

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ["BITABLE_TABLE_ID"]

# 仪表盘分享链接
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


def screenshot_dashboard(url: str) -> bytes:
    """用 Playwright 无头浏览器截图仪表盘"""
    log.info(f"打开仪表盘: {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080})

        page.goto(url, wait_until="networkidle", timeout=60000)

        # 等待仪表盘内容加载完成
        page.wait_for_timeout(5000)

        # 尝试等待仪表盘图表渲染
        try:
            page.wait_for_selector("canvas, svg, img", timeout=15000)
            page.wait_for_timeout(3000)
        except Exception:
            log.warning("未检测到图表元素，使用当前页面截图")
            page.wait_for_timeout(2000)

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

    # 获取当前北京时间作为日期
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
    log.info("飞书仪表盘自动截图存档 v6")
    log.info(f"  DASHBOARD_URL={DASHBOARD_URL}")
    log.info(f"  APP_TOKEN={BITABLE_APP_TOKEN}")
    log.info(f"  TABLE_ID={BITABLE_TABLE_ID}")
    log.info("=" * 50)

    # 1. 截图仪表盘
    screenshot_data = screenshot_dashboard(DASHBOARD_URL)

    # 2. 生成文件名
    now = datetime.now(timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"

    # 3. 获取 token
    token = get_tenant_token()

    # 4. 上传到云盘
    file_token = upload_to_drive(token, screenshot_data, filename)

    # 5. 写入 Bitable
    record_id = create_bitable_record(token, file_token)
    log.info(f"🎉 完成！record_id={record_id}, file={filename}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"❌ 运行失败: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)