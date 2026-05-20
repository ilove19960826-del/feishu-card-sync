#!/usr/bin/env python3
"""
飞书卡片图片自动存档脚本
功能：从群聊获取最新一条含图飞书卡片，下载图片并写入 Bitable 附件字段。
场景：GitHub Actions 定时运行（每天 1~2 次），无需常驻。
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

import requests

# ============================================================
# 配置（全部从环境变量读取，不硬编码）
# ============================================================
FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
CHAT_ID = os.environ["CHAT_ID"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ["BITABLE_TABLE_ID"]

# 可选：卡片标题关键词（为空则匹配所有含图卡片）
CARD_TITLE_KEYWORD = os.environ.get("CARD_TITLE_KEYWORD", "销交服试驾安全日报")

# 目标附件字段名
ATTACHMENT_FIELD = os.environ.get("ATTACHMENT_FIELD", "报告")

# 文件名前缀
FILE_NAME_PREFIX = os.environ.get("FILE_NAME_PREFIX", "试驾安全日报")

BASE_URL = "https://open.feishu.cn"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_tenant_token() -> str:
    """获取应用身份 token"""
    url = f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET,
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_token 失败: {data}")
    log.info("✅ tenant_access_token 获取成功")
    return data["tenant_access_token"]


def find_latest_image_card(token: str) -> dict | None:
    """按时间倒序拉取最近消息，找标题匹配+含图的最新卡片"""
    url = f"{BASE_URL}/open-apis/im/v1/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "container_id_type": "chat",
        "container_id": CHAT_ID,
        "sort_type": "ByCreateTimeDesc",
        "page_size": 10,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"拉取消息失败: {data}")

    items = data.get("data", {}).get("items", [])
    log.info(f"拉取到 {len(items)} 条消息")

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
        log.info(f"✅ 找到目标卡片: title={title}, image_key={image_key}")
        return {
            "image_key": image_key,
            "message_id": item["message_id"],
            "title": title,
            "create_time": item.get("create_time", "0"),
        }

    log.warning("⚠️ 未找到符合条件的含图卡片")
    return None


def _extract_image_key(content: dict) -> str | None:
    """从卡片 JSON 中提取第一个 image_key"""
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
    """通过 message resource 接口下载卡片中的图片"""
    url = f"{BASE_URL}/open-apis/im/v1/messages/{message_id}/resources/{image_key}"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"type": "image"}
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    log.info(f"图片下载成功: {len(resp.content)} bytes")
    return resp.content


def upload_to_drive(token: str, image_data: bytes, filename: str) -> str:
    """上传图片到飞书云盘，返回 file_token"""
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
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"上传到云盘失败: {data}")
    file_token = data["data"]["file_token"]
    log.info(f"✅ 图片上传成功: file_token={file_token}")
    return file_token


def create_bitable_record(token: str, file_token: str, card_title: str, create_time_ms: str) -> str:
    """在目标表新建一条记录"""
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ts_ms = int(create_time_ms) if len(create_time_ms) > 10 else int(create_time_ms) * 1000
    report_type = "日报"
    if "周报" in card_title:
        report_type = "周报"
    elif "月报" in card_title:
        report_type = "月报"
    fields = {
        "日期": ts_ms,
        "报告类型": report_type,
        ATTACHMENT_FIELD: [{"file_token": file_token}],
    }
    resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"创建 Bitable 记录失败: {data}")
    record_id = data["data"]["record"]["record_id"]
    log.info(f"✅ Bitable 记录创建成功: record_id={record_id}")
    return record_id


def main():
    log.info("=" * 50)
    log.info("飞书卡片图片自动存档 开始运行")
    log.info(f"  chat_id={CHAT_ID}  app_token={BITABLE_APP_TOKEN}  table_id={BITABLE_TABLE_ID}")
    log.info("=" * 50)

    token = get_tenant_token()
    card = find_latest_image_card(token)
    if not card:
        log.info("本次无新卡片图片，退出")
        sys.exit(0)

    image_data = download_image(token, card["image_key"], card["message_id"])
    
    ts_sec = int(card["create_time"]) // 1000 if len(card["create_time"]) > 10 else int(card["create_time"])
    dt = datetime.fromtimestamp(ts_sec, tz=timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{dt.strftime('%Y%m%d_%H%M%S')}.png"

    file_token = upload_to_drive(token, image_data, filename)
    record_id = create_bitable_record(token, file_token, card["title"], card["create_time"])

    log.info(f"🎉 全部完成！record_id={record_id}, file={filename}")


if __name__ == "__main__":
    # ========== 调试模式：逐个检查配置是否正确 ==========
    import traceback
    try:
        main()
    except Exception as e:
        log.error(f"❌ 运行失败: {e}")
        log.error(traceback.format_exc())
        # 输出环境变量是否正确（隐藏 secret）
        log.error("--- 环境变量检查 ---")
        log.error(f"FEISHU_APP_ID = {'已设置' if os.environ.get('FEISHU_APP_ID') else '❌ 未设置'}")
        log.error(f"FEISHU_APP_SECRET = {'已设置' if os.environ.get('FEISHU_APP_SECRET') else '❌ 未设置'}")
        log.error(f"CHAT_ID = {os.environ.get('CHAT_ID', '❌ 未设置')}")
        log.error(f"BITABLE_APP_TOKEN = {os.environ.get('BITABLE_APP_TOKEN', '❌ 未设置')}")
        log.error(f"BITABLE_TABLE_ID = {os.environ.get('BITABLE_TABLE_ID', '❌ 未设置')}")
        sys.exit(1)