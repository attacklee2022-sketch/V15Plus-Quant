# -*- coding: utf-8 -*-
"""
V15Plus — 云端同步模块
======================
- HMAC-SHA256 签名
- HTTPS POST 到云端
- 重试机制（3次，退避）
- outbox 补偿队列（本地保存，下次补发）
- 断网容错

使用方式：
    from _plus_.cloud_sync import CloudSyncClient
    client = CloudSyncClient()
    result = client.sync(snapshot_dict)

配置文件路径：_APP_DIR/data/cloud_config.json
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import time
import glob
from datetime import datetime
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── 路径 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_DATA_DIR = os.path.join(_APP_DIR, "data")
_OUTBOX_DIR = os.path.join(_DATA_DIR, "cloud_outbox")
_CONFIG_PATH = os.path.join(_DATA_DIR, "cloud_config.json")


# ══════════════════════════════════════════════════════
# 默认配置
# ══════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "cloud_url": "https://your-cloud-server.com/api/v1/local-snapshots",
    "client_id": "v15plus-local",
    "hmac_secret": "",  # 需在配置文件中设置
    "timeout": 15,
    "max_retries": 3,
    "retry_delay": 2.0,
    "max_outbox_age_days": 7,
}


def _load_config() -> dict:
    """加载云端配置（带缓存）。"""
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            config.update(user_config)
        except Exception:
            pass
    return config


def _save_config(config: dict) -> None:
    """保存云端配置。"""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════
# HMAC 签名
# ══════════════════════════════════════════════════════

def _sign_payload(payload: bytes, secret: str,
                  timestamp: Optional[int] = None) -> tuple[str, int]:
    ts = timestamp or int(time.time())
    message = f"{ts}.".encode("utf-8") + payload
    sig = hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return sig, ts


# ══════════════════════════════════════════════════════
# Outbox 补偿队列
# ══════════════════════════════════════════════════════

def _write_outbox(snapshot: dict) -> str:
    """写入 outbox 队列（云端不可用时的本地补偿）。"""
    os.makedirs(_OUTBOX_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(_OUTBOX_DIR, f"outbox_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, default=str)
    return path


def _list_outbox() -> list[str]:
    """列出 outbox 队列文件。"""
    os.makedirs(_OUTBOX_DIR, exist_ok=True)
    return sorted(glob.glob(os.path.join(_OUTBOX_DIR, "outbox_*.json")))


def _delete_outbox(path: str) -> None:
    """删除已发送的 outbox 文件。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _clean_outbox(max_age_days: int = 7) -> int:
    """清理超过 max_age_days 的 outbox 文件。"""
    now = time.time()
    cutoff = now - max_age_days * 86400
    cleaned = 0
    for path in _list_outbox():
        try:
            mtime = os.path.getmtime(path)
            if mtime < cutoff:
                os.remove(path)
                cleaned += 1
        except Exception:
            pass
    return cleaned


# ══════════════════════════════════════════════════════
# 同步客户端
# ══════════════════════════════════════════════════════

class CloudSyncClient:
    """
    云端同步客户端。

    职责：
    - 将快照签名后 POST 到云端
    - 失败时写入 outbox
    - 启动时自动补发 outbox
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or _load_config()
        self._init_outbox_cleanup()

    def _init_outbox_cleanup(self):
        max_age = self.config.get("max_outbox_age_days", 7)
        cleaned = _clean_outbox(max_age)
        if cleaned:
            pass  # logger 可后续添加

    @property
    def cloud_url(self) -> str:
        return self.config.get("cloud_url", DEFAULT_CONFIG["cloud_url"])

    @property
    def hmac_secret(self) -> str:
        return self.config.get("hmac_secret", "")

    @property
    def client_id(self) -> str:
        return self.config.get("client_id", DEFAULT_CONFIG["client_id"])

    # ── 单次发送 ──

    def _do_send(self, payload: bytes) -> dict:
        """
        执行单次 POST 发送。

        返回:
            {"ok": True} 或 {"ok": False, "error": "..."}
        """
        if not HAS_REQUESTS:
            return {"ok": False, "error": "requests 库未安装"}

        sig, ts = _sign_payload(payload, self.hmac_secret)
        headers = {
            "Content-Type": "application/json",
            "X-Client-Id": self.client_id,
            "X-Timestamp": str(ts),
            "X-Signature": sig,
        }
        timeout = self.config.get("timeout", DEFAULT_CONFIG["timeout"])

        try:
            resp = requests.post(
                self.cloud_url,
                data=payload,
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code in (200, 201, 204):
                return {"ok": True, "status": resp.status_code}
            else:
                return {
                    "ok": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "status": resp.status_code,
                }
        except requests.exceptions.Timeout:
            return {"ok": False, "error": "请求超时"}
        except requests.exceptions.ConnectionError:
            return {"ok": False, "error": "网络连接失败"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── 发送+重试+outbox ──

    def sync(self, snapshot: dict) -> dict:
        """
        发送快照到云端。

        流程：
        1. 先发送（含重试）
        2. 成功 → 返回 OK
        3. 失败 → 写入 outbox

        返回:
            {"ok": True, "sent": True}
            {"ok": True, "sent": False, "outbox": "路径", "error": "..."}
        """
        payload = json.dumps(snapshot, ensure_ascii=False,
                             default=str).encode("utf-8")
        max_retries = self.config.get("max_retries", DEFAULT_CONFIG["max_retries"])
        retry_delay = self.config.get("retry_delay", DEFAULT_CONFIG["retry_delay"])

        # 发送（带重试）
        for attempt in range(max_retries):
            result = self._do_send(payload)
            if result.get("ok"):
                return {"ok": True, "sent": True, "attempts": attempt + 1}
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))

        # 全部失败 → outbox
        outbox_path = _write_outbox(snapshot)
        return {
            "ok": True,
            "sent": False,
            "outbox": outbox_path,
            "error": result.get("error", "发送失败"),
            "attempts": max_retries,
        }

    # ── Outbox 补发 ──

    def flush_outbox(self) -> dict:
        """
        补发所有 outbox 中未发送的快照。

        返回:
            {"sent": N, "failed": N, "total": N, "results": [...]}
        """
        paths = _list_outbox()
        results = []
        sent = 0
        failed = 0

        for path in paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    snapshot = json.load(f)
                result = self.sync(snapshot)
                if result.get("sent"):
                    _delete_outbox(path)
                    sent += 1
                else:
                    failed += 1
                results.append({"path": path, **result})
            except Exception as e:
                failed += 1
                results.append({"path": path, "ok": False, "error": str(e)})

        return {
            "sent": sent,
            "failed": failed,
            "total": len(paths),
            "results": results,
        }

    # ── 配置管理 ──

    def configure(self, **kwargs) -> dict:
        """更新云端配置。"""
        self.config.update(kwargs)
        _save_config(self.config)

        # 敏感信息脱敏后返回
        safe = dict(self.config)
        if safe.get("hmac_secret"):
            safe["hmac_secret"] = "***"
        return {"ok": True, "config": safe}


# ══════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V15Plus 云端同步")
    parser.add_argument("action", nargs="?", default="sync",
                        choices=["sync", "flush", "configure", "status"])
    parser.add_argument("--url", help="云端 URL")
    parser.add_argument("--secret", help="HMAC 密钥")
    args = parser.parse_args()

    client = CloudSyncClient()

    if args.action == "configure":
        kwargs = {}
        if args.url:
            kwargs["cloud_url"] = args.url
        if args.secret:
            kwargs["hmac_secret"] = args.secret
        result = client.configure(**kwargs)
        print(f"[配置] 已更新: {result}")

    elif args.action == "flush":
        result = client.flush_outbox()
        print(f"[补发] 成功:{result['sent']} 失败:{result['failed']} 共:{result['total']}")
        for r in result["results"]:
            status = "OK" if r.get("sent") else "FAIL"
            print(f"  [{status}] {r.get('path', '?')}")

    elif args.action == "status":
        config = dict(client.config)
        if config.get("hmac_secret"):
            config["hmac_secret"] = "***"
        outbox_files = _list_outbox()
        print(f"[状态] 云端: {config.get('cloud_url', '未配置')}")
        print(f"[状态] Client: {config.get('client_id', '未配置')}")
        print(f"[状态] Outbox: {len(outbox_files)} 个待发送")

    else:  # sync
        from scripts.export_hybrid_snapshot import export_snapshot
        snapshot = export_snapshot()
        result = client.sync(snapshot)
        if result.get("sent"):
            print(f"[同步] 成功 (第{result['attempts']}次尝试)")
        else:
            print(f"[同步] 写入 outbox: {result.get('outbox', '?')}")
            print(f"[同步] 原因: {result.get('error', '未知')}")
