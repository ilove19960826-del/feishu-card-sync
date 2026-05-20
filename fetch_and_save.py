#!/usr/bin/env python3
"""
飞书卡片图片自动存档脚本 v5
- 多卡回退：若最新卡片图片不可用（已删除/403），自动尝试下一张
- 下载策略：resources + type=image
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


def get_tenant_token() -> str:
    url = f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_token 失败: {data}")
    log.info("✅ tenant_access_token 获取成功")
    return data["tenant_access_token"]


def list_candidate_cards(token: str) -> list[dict]:
    """返回最近的候选卡片列表"""
    url = f"{BASE_URL}/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "container_id_type": "chat",
        "container_id": CHAT_ID,
        "sort_type": "ByCreateTimeDesc",
        "page_size": 10,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"拉取消息失败: {data}")

    items = data.get("data", {}).get("items", [])
    log.info(f"拉取到 {len(items)} 条消息")

    candidates = []
    for item in items:
        if item.get("msg_type") != "interactive":
            continue
        content_str = item.get("body", {}).get("content", "{}")
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError:
            continue
        title = content.get("title", "")
        if CARD_TITLE_KEYWORD and CARD_TITLE_KEYWORD not in title:
            continue
        image_key = _extract_image_key(content)
        if not image_key:
            continue
        candidates.append({
            "image_key": image_key,
            "message_id": item["message_id"],
            "title": title,
            "create_time": item.get("create_time", "0"),
        })
    return candidates


def _extract_image_key(content: dict) -> str | None:
    for row in content.get("elements", []):
        if not isinstance(row, list):
            row = [row]
        for elem in row:
            if isinstance(elem, dict) and elem.get("tag") == "img":
                ik = elem.get("image_key")
                if ik:
                    return ik
    return None


def download_image(token: str, image_key: str, message_id: str) -> bytes:
    """通过 resources + type=image 下载图片"""
    url = f"{BASE_URL}/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, params={"type": "image"}, timeout=30)
    ct = resp.headers.get("Content-Type", "")

    if resp.status_code == 200 and ("image" in ct or len(resp.content) > 10000):
        log.info(f"✅ 图片下载成功: {len(resp.content)} bytes")
        return resp.content

    try:
        err = resp.json()
        code = err.get("code")
        msg = err.get("msg", "")
        if code == 14005:
            raise RuntimeError(f"图片已被删除 (code=14005)")
        elif code == 234008:
            raise RuntimeError(f"应用不是消息发送者 (code=234008)")
        else:
            raise RuntimeError(f"下载失败: code={code}, msg={msg}")
    except json.JSONDecodeError:
        raise RuntimeError(f"下载失败: HTTP {resp.status_code}")


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
    resp = requests.post(url, headers=headers, data=form_data, files=files, timeout=60)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"上传云盘失败: {data}")
    log.info(f"✅ 上传成功: file_token={data['data']['file_token']}")
    return data["data"]["file_token"]


def create_bitable_record(token: str, file_token: str, card_title: str, create_time_ms: str) -> str:
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ts_ms = int(create_time_ms) if len(create_time_ms) > 10 else int(create_time_ms) * 1000
    report_type = "日报"
    if "周报" in card_title:
        report_type = "周报"
    elif "月报" in card_title:
        report_type = "月报"
    fields = {"日期": ts_ms, "报告类型": report_type, ATTACHMENT_FIELD: [{"file_token": file_token}]}
    resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建记录失败: {data}")
    rid = data["data"]["record"]["record_id"]
    log.info(f"✅ 记录创建成功: record_id={rid}")
    return rid


def main():
    log.info("=" * 50)
    log.info("飞书卡片图片自动存档 v5")
    log.info(f"  APP_ID={FEISHU_APP_ID}")
    log.info(f"  CHAT_ID={CHAT_ID}")
    log.info("=" * 50)

    token = get_tenant_token()

    candidates = list_candidate_cards(token)
    if not candidates:
        log.info("⚠️ 未找到含图卡片，退出")
        sys.exit(0)

    log.info(f"候选卡片 {len(candidates)} 张，逐张尝试...")

    for idx, card in enumerate(candidates, 1):
        log.info(f"--- 第 {idx} 张: {card['title']}, img={card['image_key'][:30]}... ---")
        try:
            image_data = download_image(token, card["image_key"], card["message_id"])
            ts_sec = int(card["create_time"]) // 1000 if len(card["create_time"]) > 10 else int(card["create_time"])
            dt = datetime.fromtimestamp(ts_sec, tz=timezone(timedelta(hours=8)))
            filename = f"{FILE_NAME_PREFIX}_{dt.strftime('%Y%m%d_%H%M%S')}.png"
            file_token = upload_to_drive(token, image_data, filename)
            record_id = create_bitable_record(token, file_token, card["title"], card["create_time"])
            log.info(f"🎉 完成！record_id={record_id}, file={filename}")
            return
        except Exception as e:
            log.warning(f"  ❌ 失败: {e}，尝试下一张...")
            continue

    log.error("❌ 所有候选卡片均失败，请检查工作流是否正常发出含图卡片")
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"❌ 运行失败: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)