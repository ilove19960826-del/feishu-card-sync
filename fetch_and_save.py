#!/usr/bin/env python3
"""
飞书仪表盘图片自动存档 v11
原理：转发最新卡片 → 获取转发后的新 image_key → 下载 → 写入多维表格
"""

import os
import sys
import json
import traceback
import logging
from datetime import datetime, timezone, timedelta

import requests

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
CHAT_ID = os.environ["CHAT_ID"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ["BITABLE_TABLE_ID"]
CARD_TITLE_KEYWORD = os.environ.get("CARD_TITLE_KEYWORD", "销交服试驾安全日报")
ATTACHMENT_FIELD = os.environ.get("ATTACHMENT_FIELD", "报告")
FILE_NAME_PREFIX = os.environ.get("FILE_NAME_PREFIX", "试驾安全日报")
BASE_URL = "https://open.feishu.cn"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_token():
    r = requests.post(f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                      json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Token fail: {d}")
    log.info("OK token")
    return d["tenant_access_token"]


def find_latest_card(token):
    """找到最新一张标题匹配的含图卡片"""
    headers = {"Authorization": f"Bearer {token}"}
    params = {"container_id_type": "chat", "container_id": CHAT_ID,
              "sort_type": "ByCreateTimeDesc", "page_size": 10}
    r = requests.get(f"{BASE_URL}/open-apis/im/v1/messages",
                     headers=headers, params=params, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"List messages fail: {d}")

    for item in d.get("data", {}).get("items", []):
        if item.get("msg_type") != "interactive":
            continue
        content = json.loads(item.get("body", {}).get("content", "{}"))
        title = content.get("title", "")
        if CARD_TITLE_KEYWORD and title and CARD_TITLE_KEYWORD not in title:
            continue
        has_img = False
        for row in content.get("elements", []):
            if not isinstance(row, list):
                row = [row]
            for e in row:
                if isinstance(e, dict) and e.get("tag") == "img":
                    has_img = True
                    break
            if has_img:
                break
        if has_img:
            log.info(f"OK found card: {item['message_id']}")
            return item["message_id"]

    log.warning("No card found")
    return None


def forward_card(token, message_id):
    """转发卡片到同一个群，获得新的 message_id（图片变成标准类型）"""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(
        f"{BASE_URL}/open-apis/im/v1/messages/{message_id}/forward?receive_id_type=chat_id",
        headers=headers,
        json={"receive_id": CHAT_ID},
        timeout=15
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Forward fail: {d}")
    new_mid = d["data"]["message_id"]
    log.info(f"OK forwarded: {new_mid}")
    return new_mid


def get_forwarded_image_key(token, message_id):
    """从转发后的消息中提取 image_key"""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BASE_URL}/open-apis/im/v1/messages/{message_id}",
                     headers=headers, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Get message fail: {d}")
    content = json.loads(d["data"]["items"][0].get("body", {}).get("content", "{}"))
    for row in content.get("elements", []):
        if not isinstance(row, list):
            row = [row]
        for e in row:
            if isinstance(e, dict) and e.get("tag") == "img" and e.get("image_key"):
                log.info(f"OK new image_key: {e['image_key']}")
                return e["image_key"]
    raise RuntimeError("No image_key in forwarded card")


def download_image(token, image_key, message_id):
    """用 resources + type=image 下载（转发后的图片可以下载）"""
    url = f"{BASE_URL}/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, params={"type": "image"}, timeout=30)
    ct = r.headers.get("Content-Type", "")
    if r.status_code == 200 and ("image" in ct or len(r.content) > 10000):
        log.info(f"OK download {len(r.content)} bytes")
        return r.content
    try:
        err = r.json()
        raise RuntimeError(f"Download fail: code={err.get('code')} msg={err.get('msg')}")
    except json.JSONDecodeError:
        raise RuntimeError(f"Download fail: HTTP {r.status_code}")


def delete_message(token, message_id):
    """删除转发的消息（保持群聊干净）"""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.delete(f"{BASE_URL}/open-apis/im/v1/messages/{message_id}",
                        headers=headers, timeout=10)
    d = r.json()
    if d.get("code") == 0:
        log.info(f"OK deleted forwarded message")
    else:
        log.warning(f"Delete skipped: {d.get('msg')}")


def upload_to_drive(token, data, filename):
    r = requests.post(f"{BASE_URL}/open-apis/drive/v1/medias/upload_all",
                      headers={"Authorization": f"Bearer {token}"},
                      data={"file_name": (None, filename), "parent_type": (None, "bitable_image"),
                            "parent_node": (None, BITABLE_APP_TOKEN), "size": (None, str(len(data)))},
                      files={"file": (filename, data, "image/png")}, timeout=120)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Upload fail: {d}")
    log.info(f"OK upload {d['data']['file_token']}")
    return d["data"]["file_token"]


def create_record(token, file_token):
    now = datetime.now(timezone(timedelta(hours=8)))
    fields = {"日期": int(now.timestamp() * 1000), "报告类型": "日报",
              ATTACHMENT_FIELD: [{"file_token": file_token}]}
    r = requests.post(
        f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"fields": fields}, timeout=15)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Record fail: {d}")
    rid = d["data"]["record"]["record_id"]
    log.info(f"OK record {rid}")
    return rid


def main():
    log.info("=== v11: forward + download ===")
    token = get_token()

    # 1. 找最新卡片
    card_mid = find_latest_card(token)
    if not card_mid:
        log.warning("No card, exit")
        sys.exit(0)

    # 2. 转发卡片（生成可下载的图片副本）
    forwarded_mid = forward_card(token, card_mid)

    # 3. 从转发消息中取新 image_key
    new_image_key = get_forwarded_image_key(token, forwarded_mid)

    # 4. 下载图片
    image_data = download_image(token, new_image_key, forwarded_mid)

    # 5. 删除转发消息（保持群聊干净）
    delete_message(token, forwarded_mid)

    # 6. 上传到云盘
    now = datetime.now(timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"
    file_token = upload_to_drive(token, image_data, filename)

    # 7. 写入多维表格
    record_id = create_record(token, file_token)
    log.info(f"DONE! record_id={record_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"FAIL: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)