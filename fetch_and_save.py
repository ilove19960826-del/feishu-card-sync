#!/usr/bin/env python3
"""
飞书数据自动存档 v9
- API 获取数据 + Pillow 生成图表
- 无浏览器、无 matplotlib
"""

import os
import sys
import json
import traceback
import logging
from datetime import datetime, timezone, timedelta
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]
BITABLE_APP_TOKEN = os.environ["BITABLE_APP_TOKEN"]
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "tbl8NHhZF584wTwt")
ATTACHMENT_FIELD = os.environ.get("ATTACHMENT_FIELD", "报告")
FILE_NAME_PREFIX = os.environ.get("FILE_NAME_PREFIX", "试驾安全日报")
BASE_URL = "https://open.feishu.cn"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def get_tenant_token() -> str:
    resp = requests.post(f"{BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                         json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    log.info("✅ token 获取成功")
    return data["tenant_access_token"]


def fetch_records(token: str, table_id: str) -> list[dict]:
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    all_records = []
    page_token = None
    while True:
        body = {"page_size": 500}
        if page_token:
            body["page_token"] = page_token
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            log.warning(f"获取记录失败: {data.get('code')} {data.get('msg')}")
            break
        all_records.extend(data.get("data", {}).get("items", []))
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data["data"].get("page_token")
    log.info(f"获取到 {len(all_records)} 条记录")
    return all_records


def list_tables_and_fields(token: str):
    resp = requests.get(f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables",
                        headers={"Authorization": f"Bearer {token}"}, timeout=15)
    tables = resp.json().get("data", {}).get("items", [])
    log.info(f"=== 共 {len(tables)} 张表 ===")
    for t in tables:
        log.info(f"  {t['table_id']} | {t['name']}")
        fr = requests.get(f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{t['table_id']}/fields",
                          headers={"Authorization": f"Bearer {token}"}, timeout=15)
        for f in fr.json().get("data", {}).get("items", []):
            log.info(f"    {f['field_name']} (type={f['type']})")
    return tables


def generate_report_image(records: list[dict]) -> bytes:
    W, H = 1200, 800
    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    now = datetime.now(timezone(timedelta(hours=8)))
    title = f"Trial Drive Safety Report - {now.strftime('%Y/%m/%d')}"

    try:
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_mid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font_big = ImageFont.load_default()
        font_mid = font_big
        font_sm = font_big

    draw.rectangle([(0, 0), (W, 60)], fill="#2980B9")
    draw.text((30, 15), title, fill="#FFFFFF", font=font_big)

    total = len(records)
    y = 90
    draw.text((30, y), f"Total Records: {total}", fill="#333333", font=font_mid)
    y += 40

    field_stats = {}
    for r in records:
        fields = r.get("fields", {})
        for k, v in fields.items():
            if isinstance(v, str) and v:
                field_stats.setdefault(k, {})
                field_stats[k][v] = field_stats[k].get(v, 0) + 1

    bar_y = y + 10
    colors = ["#3498DB", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6", "#1ABC9C"]
    for i, (field_name, value_counts) in enumerate(list(field_stats.items())[:6]):
        color = colors[i % len(colors)]
        draw.text((30, bar_y), f"{field_name}:", fill="#333333", font=font_mid)
        bar_y += 30
        max_count = max(value_counts.values()) if value_counts else 1
        for val, cnt in list(value_counts.items())[:5]:
            bar_w = int(cnt / max_count * 600) if max_count > 0 else 0
            draw.text((50, bar_y), f"{val}: {cnt}", fill="#555555", font=font_sm)
            draw.rectangle([(300, bar_y), (300 + bar_w, bar_y + 20)], fill=color)
            draw.text((310 + bar_w, bar_y), str(cnt), fill="#333333", font=font_sm)
            bar_y += 28
        bar_y += 20

    draw.line([(0, H - 40), (W, H - 40)], fill="#CCCCCC", width=1)
    draw.text((30, H - 35), f"Auto-generated | {now.strftime('%Y-%m-%d %H:%M')}", fill="#999999", font=font_sm)

    buf = BytesIO()
    img.save(buf, format="PNG", dpi=(150, 150))
    buf.seek(0)
    log.info(f"✅ 图表生成: {len(buf.getvalue())} bytes")
    return buf.getvalue()


def upload_to_drive(token: str, data: bytes, filename: str) -> str:
    resp = requests.post(f"{BASE_URL}/open-apis/drive/v1/medias/upload_all",
                         headers={"Authorization": f"Bearer {token}"},
                         data={"file_name": (None, filename), "parent_type": (None, "bitable_image"),
                               "parent_node": (None, BITABLE_APP_TOKEN), "size": (None, str(len(data)))},
                         files={"file": (filename, data, "image/png")}, timeout=120)
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"上传失败: {d}")
    log.info(f"✅ 上传成功: {d['data']['file_token']}")
    return d["data"]["file_token"]


def create_record(token: str, file_token: str) -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    fields = {"日期": int(now.timestamp() * 1000), "报告类型": "日报",
              ATTACHMENT_FIELD: [{"file_token": file_token}]}
    resp = requests.post(f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records",
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                         json={"fields": fields}, timeout=15)
    d = resp.json()
    if d.get("code") != 0:
        raise RuntimeError(f"创建记录失败: {d}")
    rid = d["data"]["record"]["record_id"]
    log.info(f"✅ 记录创建成功: {rid}")
    return rid


def main():
    log.info("=" * 50)
    log.info("飞书数据自动存档 v9")
    log.info("=" * 50)
    token = get_tenant_token()
    list_tables_and_fields(token)
    records = fetch_records(token, BITABLE_TABLE_ID)
    chart = generate_report_image(records)
    now = datetime.now(timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"
    file_token = upload_to_drive(token, chart, filename)
    record_id = create_record(token, file_token)
    log.info(f"🎉 完成！record_id={record_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"❌ 失败: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)