#!/usr/bin/env python3
"""
飞书仪表盘数据自动存档 v8
- 用 Bitable API 直接获取数据
- 用 matplotlib 生成图表
- 不依赖浏览器/截图
"""

import os
import sys
import json
import traceback
import logging
from datetime import datetime, timezone, timedelta

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO

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


def fetch_bitable_records(token: str, table_id: str, filter_str: str = None) -> list[dict]:
    """分页获取 Bitable 记录"""
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/records/search"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    all_records = []
    page_token = None

    while True:
        body = {"page_size": 100}
        if filter_str:
            body["filter"] = json.loads(filter_str)
        if page_token:
            body["page_token"] = page_token

        resp = requests.post(url, headers=headers, json=body, timeout=30)
        data = resp.json()
        if data.get("code") != 0:
            log.warning(f"获取记录失败: code={data.get('code')}, msg={data.get('msg')}")
            break

        records = data.get("data", {}).get("items", [])
        all_records.extend(records)

        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token")

    log.info(f"获取到 {len(all_records)} 条记录 (table={table_id})")
    return all_records


def list_tables(token: str) -> list[dict]:
    """列出所有数据表"""
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        log.warning(f"获取表列表失败: {data}")
        return []
    tables = data.get("data", {}).get("items", [])
    log.info(f"共 {len(tables)} 张表:")
    for t in tables:
        log.info(f"  {t['table_id']} | {t['name']}")
    return tables


def list_fields(token: str, table_id: str) -> list[dict]:
    """列出表字段"""
    url = f"{BASE_URL}/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{table_id}/fields"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        return []
    fields = data.get("data", {}).get("items", [])
    log.info(f"表 {table_id} 的字段:")
    for f in fields:
        log.info(f"  {f['field_id']} | {f['field_name']} | type={f['type']}")
    return fields


def generate_daily_chart(records: list[dict], target_date: str = None) -> bytes:
    """根据事故快报数据生成日报图表"""
    # 设置中文字体
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
    plt.rcParams["axes.unicode_minus"] = False

    # 获取最近 7 天的数据
    now = datetime.now(timezone(timedelta(hours=8)))
    dates = []
    had_count = []   # HAD 事故
    responsible = []  # 有责事故
    no_fault = []     # 无责事故

    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        date_str = d.strftime("%Y/%m/%d")
        dates.append(d)
        # 从 records 中统计当天数据（这里用模拟数据，实际需要根据字段名匹配）
        day_count_had = 0
        day_count_resp = 0
        day_count_nofault = 0
        for r in records:
            fields = r.get("fields", {})
            # 尝试匹配日期字段
            for k, v in fields.items():
                if isinstance(v, (int, float)):
                    # DateTime 字段（毫秒时间戳）
                    try:
                        record_dt = datetime.fromtimestamp(v / 1000, tz=timezone(timedelta(hours=8)))
                        if record_dt.strftime("%Y/%m/%d") == date_str:
                            # 根据字段内容计数
                            pass
                    except Exception:
                        pass
        had_count.append(day_count_had)
        responsible.append(day_count_resp)
        no_fault.append(day_count_nofault)

    # 创建图表
    fig, ax = plt.subplots(figsize=(12, 6))

    x = range(len(dates))
    x_labels = [d.strftime("%m/%d") for d in dates]

    ax.bar([i - 0.25 for i in x], had_count, width=0.25, label="HAD", color="#FF6B6B")
    ax.bar(x, responsible, width=0.25, label="Responsible", color="#FFA07A")
    ax.bar([i + 0.25 for i in x], no_fault, width=0.25, label="No Fault", color="#98D8C8")

    ax.set_xlabel("Date")
    ax.set_ylabel("Count")
    ax.set_title("Trial Drive Safety Daily Report")
    ax.set_xticks(list(x))
    ax.set_xticklabels(x_labels)
    ax.legend()

    plt.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)

    log.info(f"✅ 图表生成成功: {len(buf.getvalue())} bytes")
    return buf.getvalue()


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
    log.info("飞书仪表盘数据自动存档 v8")
    log.info(f"  APP_TOKEN={BITABLE_APP_TOKEN}")
    log.info(f"  TABLE_ID={BITABLE_TABLE_ID}")
    log.info("=" * 50)

    token = get_tenant_token()

    # 1. 列出所有表，找到数据表
    tables = list_tables(token)

    # 2. 获取事故快报数据（核心数据表）
    source_table_id = "tbl8NHhZF584wTwt"  # （EHS团队填写）事故快报
    fields = list_fields(token, source_table_id)
    records = fetch_bitable_records(token, source_table_id)

    # 3. 生成图表
    chart_data = generate_daily_chart(records)

    # 4. 上传
    now = datetime.now(timezone(timedelta(hours=8)))
    filename = f"{FILE_NAME_PREFIX}_{now.strftime('%Y%m%d_%H%M%S')}.png"
    file_token = upload_to_drive(token, chart_data, filename)

    # 5. 写入目标表
    record_id = create_bitable_record(token, file_token)
    log.info(f"🎉 完成！record_id={record_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error(f"❌ 运行失败: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)