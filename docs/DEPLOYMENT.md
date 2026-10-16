# 部署指引（云端采集 / 上传 / 推送）

V15Plus 支持两种使用方式：

1. **纯本地**：只跑 GUI、扫描、回测，不需要任何服务器。
2. **本地 + 云端**：本机负责采集与计算，云服务器负责长期存储、Web 面板与消息推送。

本页说明第 2 种的架构与搭建方法。**不需要云端功能可忽略本页。**

## 架构总览

```
┌──────────────── 本机（Windows） ────────────────┐
│  GUI / 每日扫描 / 回测                          │
│  collect_realtime.py       实时行情采集          │
│  v15plus_auto_pipeline.py  全流程 + 快照导出      │
│  proxy_tunnel.py           HTTP 代理 + SSH 反向隧道│
└───────────────┬─────────────────────────────────┘
                │ SSH/SFTP 上传（HMAC 签名）
                ▼
┌────────────── 云服务器（Ubuntu） ───────────────┐
│  market-bot       行情接收 + 消息推送            │
│  Hybrid Research  快照存储 + 融合评分 + Web 面板  │
│  worker           后台计算进程                   │
└─────────────────────────────────────────────────┘
```

## 为什么要反向代理隧道

部分行情源（如东方财富）会对数据中心 IP 做限流。`proxy_tunnel.py` 的做法是：

1. 在本机开一个 HTTP 代理（默认 8888）
2. 用 SSH 反向隧道（`-R`）把云服务器的 8888 映射到本机的 8888
3. 云服务器设置 `HTTP_PROXY=http://localhost:8888`，采集流量经本机出口

这样云服务器就能"借"本机的网络访问行情源。

### 使用

配置好 `CLOUD_HOST` / `CLOUD_USER` / `CLOUD_PASSWORD` 等环境变量后：

```bash
python scripts/proxy_tunnel.py               # 代理 + 隧道
python scripts/proxy_tunnel.py --no-tunnel   # 只开代理，不建隧道
```

> 隧道依赖本机的 `ssh`；用密码认证时需 `sshpass`，推荐改用密钥认证。

## 部署步骤（通用）

### 1. 准备云服务器

- Ubuntu 22.04+，安装 Python 3.11+
- 开放所需端口（如 80、8000、8001）
- 配置 SSH 密钥登录，创建部署用户

### 2. 部署云端服务

`market-bot` 与 `Hybrid Research` 是配合本机的云端服务（**不在本仓库内**，
需按你的实际架构自行部署）。关键约定：

- market-bot 提供 `POST /api/v1/market-data` 接收行情快照
- Hybrid Research 提供上传签名的 HMAC 校验，密钥与本机 `HYBRID_HMAC_SECRET` 一致
- 两者的数据目录分别对应本机的 `CLOUD_MARKETBOT_DIR` / `CLOUD_REMOTE_DIR`

### 3. 配置本机环境变量

参考 [CONFIGURATION.md](CONFIGURATION.md)，至少配置：

```
CLOUD_URL / CLOUD_HOST / CLOUD_USER / CLOUD_SSH_KEY / HYBRID_HMAC_SECRET
```

### 4. 测试上传

```bash
python scripts/manual_upload_v15.py      # 手动上传一次快照
python scripts/collect_realtime.py       # 采集并上传实时行情
```

### 5. 配置自动任务（Windows）

以管理员身份运行：

```powershell
.\安装计划任务.ps1
```

会在「任务计划程序」中注册：K 线监控 + 收盘扫描、实盘定时新闻上传等任务。

## 安全提醒

- 上传密钥（HMAC）与 SSH 凭证都通过**环境变量**提供，不要写死在脚本里
- 云服务器上的应用密钥同样用环境变量注入，不要提交到任何仓库
- 定期轮换密钥；一旦怀疑泄漏，立即吊销重建
