# 配置指引

V15Plus 的核心评分与 GUI **无需任何配置**即可运行。本页说明需要 AI、云端同步、
消息推送等功能时如何配置。

所有敏感配置一律通过**环境变量**读取，源码中不含任何真实凭证。
推荐做法：复制 `.env.example` 为 `.env` 并填写，或用系统环境变量导出。

> ⚠️ `.env` 已在 `.gitignore` 中排除，**切勿提交真实密钥**。

## 1. AI 分析（DeepSeek）

用于新闻蒸馏、AI 解读、自动交易决策等。

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_KEY` | DeepSeek API Key |

- **获取方式**：登录 [DeepSeek 开放平台](https://platform.deepseek.com/) → API Keys → 创建
- **多 Key 轮换**：项目支持 `DEEPSEEK_KEY1` / `DEEPSEEK_KEY2` / `DEEPSEEK_KEY3`，
  GUI 会按顺序选取第一个有效的 Key
- **备用文件方式**：也可在程序目录放置 `deepseek_key1.txt`（每行一个 Key，`#` 开头为注释）
- **不配置的后果**：AI 相关功能自动跳过，核心评分不受影响

## 2. 云服务器（Hybrid Research / market-bot）

用于把本机扫描结果上传到云服务器，实现远端面板与消息推送。
若不需要云端功能，**跳过本节即可**。

| 变量 | 说明 | 示例 |
|---|---|---|
| `CLOUD_URL` | 云端服务基址 | `http://your-server.example.com/research` |
| `CLOUD_HOST` | 服务器 IP / 域名（SSH 用） | `your-server.example.com` |
| `CLOUD_USER` | SSH 用户名 | `ubuntu` |
| `CLOUD_PASSWORD` | SSH 密码（建议改用密钥） | 留空 |
| `CLOUD_SSH_KEY` | SSH 私钥路径 | `~/.ssh/id_ed25519` |
| `CLOUD_REMOTE_DIR` | Hybrid Research 远端目录 | `/home/ubuntu/hybrid-research` |
| `CLOUD_MARKETBOT_DIR` | market-bot 远端目录 | `/home/ubuntu/market-bot` |
| `MARKETBOT_URL` | market-bot 上传接口 | `http://your-server.example.com/api/v1/market-data` |
| `HYBRID_HMAC_SECRET` | 上传签名密钥（与服务器端一致） | 自行生成强随机串 |
| `HYBRID_PORT` | Hybrid 服务端口 | `8001` |
| `APP_ENV` | 运行环境 | `production` |

- **HMAC 签名**：上传快照时会用 `HYBRID_HMAC_SECRET` 对 `时间戳 + 载荷` 做 SHA256 签名，
  服务器端用相同密钥校验。两端必须一致。
- **SSH 认证**：优先使用密钥认证（`CLOUD_SSH_KEY`），比密码更安全。
- **推荐**：不要在命令行明文传密钥，改用 `.env` 或服务器端的环境变量注入。

## 3. 飞书机器人推送

用于把盘面结论推送到飞书群。

| 变量 | 说明 |
|---|---|
| `FEISHU_APP_ID` | 飞书自建应用 App ID（`cli_` 开头） |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_CHAT_ID` | 目标群 Chat ID（`oc_` 开头） |

- **获取方式**：飞书开放平台创建企业自建应用，开通机器人能力与消息权限
- **安全**：App Secret 等同密码，务必只放在环境变量里，切勿写进源码或提交仓库

## 4. 企业微信

| 变量 | 说明 |
|---|---|
| `WECHAT_TOKEN` | 企业微信回调校验 Token |

## 环境变量加载顺序建议

代码通过 `os.environ` 读取。设置方式（任选）：

- **Windows（当前会话）**：`set DEEPSEEK_KEY=sk-xxxx`
- **Windows（永久）**：`setx DEEPSEEK_KEY "sk-xxxx"`
- **PowerShell**：`$env:DEEPSEEK_KEY = "sk-xxxx"`
- **Linux / macOS**：`export DEEPSEEK_KEY=sk-xxxx`

若使用 `.env` 文件，请在启动脚本中加载（例如 `python-dotenv`，或启动前 `source`/`set`）。

## 安全检查清单

开源/分享你的改动前，确认：

- [ ] `.env` 没有被提交（`.gitignore` 已包含）
- [ ] 源码中没有任何硬编码的 Key / 密码 / IP
- [ ] `deepseek_key*.txt`、`*.pem`、`*.key` 等未入库
- [ ] 若曾把真实密钥写入过文件，**及时到各平台轮换（吊销重建）**
