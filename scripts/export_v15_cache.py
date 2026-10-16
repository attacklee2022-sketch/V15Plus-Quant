# -*- coding: utf-8 -*-
"""
V15Plus 板块数据导出 → 云端
============================
在跑完板块资金流 Tab 后执行：
    python scripts/export_v15_cache.py

采集 V15Plus T1 z-score 数据 → 通过 SSH 上传到云端服务器。
"""
import os, sys, json, time
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_INTERNAL = os.path.join(_APP_DIR, "_internal")
_PLUS = os.path.join(_APP_DIR, "_plus_")
for p in [_INTERNAL, _PLUS, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

# 云端服务器配置
CLOUD_HOST = os.environ.get("CLOUD_HOST", "your-server.example.com")
CLOUD_USER = os.environ.get("CLOUD_USER", "ubuntu")
CLOUD_DIR = os.environ.get("CLOUD_MARKETBOT_DIR", "/home/ubuntu/market-bot/data")
SSH_KEY_PATH = os.path.expanduser(os.environ.get("CLOUD_SSH_KEY", "~/.ssh/id_ed25519"))

print("=" * 50)
print("V15Plus 板块数据导出 -> 云端")
print("时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 50)

# Step 1: 获取 T1 z-score 板块数据
print("\n[1/3] 采集板块数据...")
from _plus_.sector_power import get_flow_chart_data, _compute_all_t1_scores
_compute_all_t1_scores()
chart = get_flow_chart_data()
daily = chart.get("daily", [])
print(f"  T1 板块数据: {len(daily)} 条")

# Step 2: 组装 JSON
print("[2/3] 组装 JSON...")
sectors = []
for item in daily:
    share, z_val, tdx_code, name, dz, prev = item
    sectors.append({
        "name": name,
        "tdx_code": tdx_code,
        "share_pct": round(share, 2),
        "z_score": round(z_val, 2),
        "dz": round(dz, 4),
        "prev_dz": round(prev, 4) if prev else 0,
    })
sectors.sort(key=lambda x: -x["share_pct"])

payload = {
    "date": datetime.now().strftime("%Y%m%d"),
    "time": datetime.now().strftime("%H:%M:%S"),
    "sectors": sectors,
    "total_count": len(sectors),
}

data_str = json.dumps(payload, ensure_ascii=False)
print(f"  {len(sectors)} 个板块, {len(data_str)} 字节")

# Step 3: 上传到云端（通过 SSH 密钥认证）
print("[3/3] 上传到云端...")
try:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(CLOUD_HOST, 22, CLOUD_USER, timeout=10,
                   allow_agent=False, look_for_keys=False,
                   key_filename=SSH_KEY_PATH)

    # 直接在服务器上写入文件
    date_str = payload["date"]
    remote_path = f"{CLOUD_DIR}/v15_{date_str}.json"

    sftp = client.open_sftp()
    with sftp.open(remote_path, "w") as f:
        f.write(data_str)
    sftp.close()
    client.close()

    print(f"  [OK] 上传成功！云端路径: {remote_path}")
    print(f"  [OK] 共 {len(sectors)} 个板块")

except Exception as e:
    print(f"  [ERROR] 上传失败: {e}")
    # 本地备份
    local_path = os.path.join(_APP_DIR, "data", f"v15_cache_{date_str}.json")
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(data_str)
    print(f"  [OK] 已保存到本地: {local_path}")

# Top 5 预览
print()
print("-" * 50)
print(f"Top 5 板块:")
for s in sectors[:5]:
    print(f"  {s['name']}: 占比{s['share_pct']:.1f}% z={s['z_score']:+.2f} dz={s['dz']:+.4f}")
print("-" * 50)
