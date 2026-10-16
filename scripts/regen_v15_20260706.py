#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新计算 7/6 V15 数据并上传到 market-bot。"""
import os, sys, json, time
from datetime import datetime, timedelta

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in [os.path.join(_APP_DIR, "_internal"), os.path.join(_APP_DIR, "_plus_"), _APP_DIR]:
    if p not in sys.path: sys.path.insert(0, p)

TARGET_DATE = "20260706"
BACK_DATE = datetime.strptime(TARGET_DATE, "%Y%m%d") - timedelta(days=1)

print(f"重新计算 {TARGET_DATE} V15 数据...")

# 设置计算日期环境
os.environ["V15_BACKTEST_DATE"] = TARGET_DATE

from _plus_.sector_power import get_flow_chart_data, _compute_all_t1_scores, get_sector_fund_score
_compute_all_t1_scores()
chart = get_flow_chart_data()
daily = chart.get("daily", [])
print(f"  板块数: {len(daily)}")

sectors = []
for item in daily:
    share, z_val, tdx_code, name, dz, prev = item
    t2 = {"t2_score": 50, "t2_label": "", "vol_ratio": 1.0, "trend_label": ""}
    try:
        fs = get_sector_fund_score(tdx_code)
        t2 = {"t2_score": round(fs.get("score", 50), 1), "t2_label": fs.get("label", ""),
              "vol_ratio": round(fs.get("vol_ratio", 1.0), 2), "trend_label": fs.get("trend_label", "")}
    except: pass
    sectors.append({"name": name, "tdx_code": tdx_code, "share_pct": round(share, 2),
                    "z_score": round(z_val, 2), "dz": round(dz, 4), "prev_dz": round(prev, 4) if prev else 0,
                    "t2_score": t2["t2_score"], "t2_label": t2["t2_label"],
                    "vol_ratio": t2["vol_ratio"], "trend_label": t2["trend_label"]})
sectors.sort(key=lambda x: -x["share_pct"])
payload = {"date": TARGET_DATE, "time": "15:30:00", "sectors": sectors, "total_count": len(sectors)}

import paramiko
ssh_key = os.path.expanduser(os.environ.get("CLOUD_SSH_KEY", "~/.ssh/id_ed25519"))
cloud_host = os.environ.get("CLOUD_HOST", "")
cloud_user = os.environ.get("CLOUD_USER", "ubuntu")
remote_dir = os.environ.get("CLOUD_MARKETBOT_DIR", "/home/ubuntu/market-bot")
client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(cloud_host, 22, cloud_user, timeout=10,
               allow_agent=False, look_for_keys=False, key_filename=ssh_key)
sftp = client.open_sftp()
remote_path = f"{remote_dir}/data/v15_{TARGET_DATE}.json"
import tempfile
with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
    json.dump(payload, f, ensure_ascii=False); tmp_path = f.name
sftp.put(tmp_path, remote_path)
sftp.close(); client.close()
os.unlink(tmp_path)
print(f"  上传成功: {remote_path} ({len(sectors)}个板块)")
print("完成")
