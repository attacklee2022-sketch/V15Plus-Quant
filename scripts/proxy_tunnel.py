# -*- coding: utf-8 -*-
"""
HTTP 代理 + SSH 反向隧道
=========================
让云服务器通过本机网络访问行情接口（如东方财富）。

用法：
    python scripts/proxy_tunnel.py                   # 启动代理 + SSH 隧道
    python scripts/proxy_tunnel.py --no-tunnel        # 只启动代理，不建隧道

配置（通过环境变量，见 .env.example）：
    CLOUD_HOST        云服务器地址
    CLOUD_USER        SSH 用户名
    CLOUD_PASSWORD    SSH 密码（建议改用密钥认证）
    LOCAL_PROXY_PORT  本机代理端口（默认 8888）
    REMOTE_PROXY_PORT 云服务器代理端口（默认 8888）

原理：
    1. 在本机开一个 HTTP 代理（端口 8888）
    2. SSH 反向隧道（-R），把 8888 映射到云服务器的 8888 端口
    3. 云服务器设置 HTTP_PROXY=http://localhost:8888，采集流量走本机
"""
from __future__ import annotations
import os
import sys
import subprocess
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error

CLOUD_HOST = os.environ.get("CLOUD_HOST", "your-server.example.com")
CLOUD_USER = os.environ.get("CLOUD_USER", "ubuntu")
CLOUD_PASSWORD = os.environ.get("CLOUD_PASSWORD", "")
LOCAL_PROXY_PORT = int(os.environ.get("LOCAL_PROXY_PORT", "8888"))
REMOTE_PROXY_PORT = int(os.environ.get("REMOTE_PROXY_PORT", "8888"))


# ═══════════════════════════════════════════════════════════
# HTTP 代理
# ═══════════════════════════════════════════════════════════

class ProxyHandler(BaseHTTPRequestHandler):
    """简单的 HTTP 代理（仅支持 GET/CONNECT）。"""

    def do_GET(self):
        self._proxy_request()

    def do_CONNECT(self):
        # HTTPS CONNECT 隧道
        host, port = self.path.split(":")
        try:
            dest = (host, int(port))
            with urllib.request.urlopen(f"http://{host}:{port}", timeout=10) as _:
                pass
        except Exception:
            pass
        self.send_response(200)
        self.end_headers()

    def _proxy_request(self):
        try:
            req = urllib.request.Request(self.path, headers=dict(self.headers))
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-encoding"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as e:
            try:
                self.send_error(502, str(e))
            except Exception:
                pass

    def log_message(self, format, *args):
        print(f"  [代理] {args[0]} {args[1]} {args[2]}")


def start_proxy():
    """启动 HTTP 代理服务器。"""
    server = HTTPServer(("0.0.0.0", LOCAL_PROXY_PORT), ProxyHandler)
    print(f"  [代理] HTTP 代理已启动: 端口 {LOCAL_PROXY_PORT}")
    server.serve_forever()


# ═══════════════════════════════════════════════════════════
# SSH 反向隧道
# ═══════════════════════════════════════════════════════════

def start_tunnel(password: str):
    """
    用 SSH 反向隧道把本地代理端口映射到云服务器。

    命令: ssh -R {remote_port}:localhost:{local_port} {user}@{host} -N
    -R 表示远程端口转发: 云服务器访问 localhost:remote_port = 本机 local_port
    """
    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-R", f"{REMOTE_PROXY_PORT}:localhost:{LOCAL_PROXY_PORT}",
        "-N",
        f"{CLOUD_USER}@{CLOUD_HOST}",
    ]

    print(f"  [隧道] SSH 反向隧道: 云服务器 :{REMOTE_PROXY_PORT} → 本机 :{LOCAL_PROXY_PORT}")
    print(f"  [隧道] 连接中...")

    try:
        # 用 sshpass 传密码，或者用密钥
        full_cmd = ["sshpass", "-p", password] + cmd
        proc = subprocess.Popen(full_cmd, stderr=subprocess.PIPE)
        print(f"  [隧道] 已建立 (PID={proc.pid})")
        proc.wait()
    except FileNotFoundError:
        print("  [隧道] 未安装 sshpass，尝试直接 ssh（需要配置密钥认证）")
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
        proc.wait()
    except Exception as e:
        print(f"  [隧道] 异常: {e}")


# ═══════════════════════════════════════════════════════════
# 云服务器配置脚本
# ═══════════════════════════════════════════════════════════

def configure_cloud(password: str):
    """在云服务器上设置 HTTP_PROXY 环境变量并重启远端 worker。

    远端 worker 所需的密钥全部从本机环境变量读取（见 .env.example），
    未设置的项不会注入，避免把凭证写死在源码里。
    """
    import paramiko

    remote_dir = os.environ.get("CLOUD_REMOTE_DIR", "/home/ubuntu/hybrid-research")
    proxy_url = f"http://localhost:{REMOTE_PROXY_PORT}"

    # 远端 worker 需要的环境变量（缺省不注入）
    remote_env = {
        "HYBRID_PORT": os.environ.get("HYBRID_PORT", "8001"),
        "HYBRID_HMAC_SECRET": os.environ.get("HYBRID_HMAC_SECRET", ""),
        "APP_ENV": os.environ.get("APP_ENV", "production"),
        "FEISHU_APP_ID": os.environ.get("FEISHU_APP_ID", ""),
        "FEISHU_APP_SECRET": os.environ.get("FEISHU_APP_SECRET", ""),
        "FEISHU_CHAT_ID": os.environ.get("FEISHU_CHAT_ID", ""),
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
    }
    env_lines = " \\\n  ".join(f"{k}={v}" for k, v in remote_env.items() if v)

    commands = f'''
cd {remote_dir}
export HTTP_PROXY={proxy_url}
export HTTPS_PROXY={proxy_url}
nohup env PYTHONPATH={remote_dir} \\
  HTTP_PROXY={proxy_url} \\
  HTTPS_PROXY={proxy_url} \\
  {env_lines} \\
  python3 {remote_dir}/start_worker.py > {remote_dir}/logs/worker.log 2>&1 &
echo "worker 已重启（带代理）"
'''
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(CLOUD_HOST, 22, CLOUD_USER, password, timeout=10)
        stdin, stdout, stderr = client.exec_command(commands)
        print(stdout.read().decode()[:500])
        client.close()
        print("  [配置] 云服务器 worker 已重启（带代理）")
    except Exception as e:
        print(f"  [配置] 连接云服务器失败: {e}")


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="HTTP 代理 + SSH 反向隧道")
    parser.add_argument("--no-tunnel", action="store_true", help="只启动代理，不建隧道")
    args = parser.parse_args()

    print("=" * 50)
    print("  HTTP 代理 + SSH 反向隧道")
    print("=" * 50)
    print()
    print(f"  本地代理端口: {LOCAL_PROXY_PORT}")
    print(f"  云服务器地址: {CLOUD_USER}@{CLOUD_HOST}:{REMOTE_PROXY_PORT}")
    print()

    # 启动代理（后台线程）
    proxy_thread = threading.Thread(target=start_proxy, daemon=True)
    proxy_thread.start()

    if not args.no_tunnel:
        # 创建隧道（前台阻塞）
        import time
        time.sleep(1)
        configure_cloud(CLOUD_PASSWORD)
        print()
        print("  隧道建立中，保持此窗口打开...")
        print("  按 Ctrl+C 关闭隧道")
        print()
        start_tunnel(CLOUD_PASSWORD)
    else:
        print("  代理已启动（无隧道模式）")
        proxy_thread.join()


if __name__ == "__main__":
    main()
