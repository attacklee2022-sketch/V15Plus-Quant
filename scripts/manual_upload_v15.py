#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手动触发 V15 数据计算并上传到 market-bot 云端。"""
import os, sys, json, time
from datetime import datetime

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in [os.path.join(_APP_DIR, "_internal"), os.path.join(_APP_DIR, "_plus_"), _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

print("=" * 50)
print(f"  V15 手动上传")
print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
print("=" * 50)

# 1. 计算板块资金流
print("\n[1/4] 计算板块资金流...")
from _plus_.sector_power import get_flow_chart_data, _compute_all_t1_scores, get_sector_fund_score
_compute_all_t1_scores()
chart = get_flow_chart_data()
daily = chart.get("daily", [])
print(f"  {len(daily)} 个板块")

# 2. 组装 payload
print("\n[2/4] 组装 payload...")
sectors = []
for item in daily:
    share, z_val, tdx_code, name, dz, prev = item
    t2 = {"t2_score": 50, "t2_label": "", "vol_ratio": 1.0, "trend_label": ""}
    try:
        fs = get_sector_fund_score(tdx_code)
        t2 = {
            "t2_score": round(fs.get("score", 50), 1),
            "t2_label": fs.get("label", ""),
            "vol_ratio": round(fs.get("vol_ratio", 1.0), 2),
            "trend_label": fs.get("trend_label", ""),
        }
    except:
        pass
    sectors.append({
        "name": name, "tdx_code": tdx_code,
        "share_pct": round(share, 2), "z_score": round(z_val, 2),
        "dz": round(dz, 4), "prev_dz": round(prev, 4) if prev else 0,
        "t2_score": t2["t2_score"], "t2_label": t2["t2_label"],
        "vol_ratio": t2["vol_ratio"], "trend_label": t2["trend_label"],
    })
sectors.sort(key=lambda x: -x["share_pct"])
payload = {
    "date": datetime.now().strftime("%Y%m%d"),
    "time": datetime.now().strftime("%H:%M:%S"),
    "sectors": sectors, "total_count": len(sectors),
}
print(f"  {len(sectors)} 个板块, 日期: {payload['date']}")

# 3. 本地保存备份
print("\n[3/4] 本地保存...")
backup_dir = os.path.join(_APP_DIR, "data", "cloud_outbox")
os.makedirs(backup_dir, exist_ok=True)
backup_path = os.path.join(backup_dir, f"v15_{payload['date']}_{payload['time'].replace(':','')}.json")
with open(backup_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(f"  已保存: {backup_path}")

# 4. SSH 上传到 market-bot
print("\n[4/4] 上传到 market-bot...")
import paramiko
ssh_key = os.path.expanduser(os.environ.get("CLOUD_SSH_KEY", "~/.ssh/id_ed25519"))
cloud_host = os.environ.get("CLOUD_HOST", "")
cloud_user = os.environ.get("CLOUD_USER", "ubuntu")
remote_dir = os.environ.get("CLOUD_MARKETBOT_DIR", "/home/ubuntu/market-bot")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(cloud_host, 22, cloud_user,
               timeout=10, allow_agent=False, look_for_keys=False,
               key_filename=ssh_key)
sftp = client.open_sftp()
remote_path = f"{remote_dir}/data/v15_{payload['date']}.json"
sftp.put(backup_path, remote_path)
sftp.close()
client.close()
print(f"  上传成功: {remote_path} ({len(sectors)}个板块)")

print(f"\n[完成] {datetime.now():%Y-%m-%d %H:%M:%S}")
