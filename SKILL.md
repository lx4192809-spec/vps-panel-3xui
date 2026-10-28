---
name: vps-panel-3xui
description: 在一台 Linux VPS 上全自动部署、重装或迁移 3x-ui（x-ui）代理面板——SSH 探测、非交互安装、验收、备份数据库、凭据落盘一次做完。当用户提供 VPS 的 IP / SSH 端口 / root 密码，并说"搭面板""装 3x-ui""节点面板""重装面板""续费后恢复面板""帮我配代理服务器"时使用；也用于排查面板打不开、密码登不上、端口不通、HTTP/HTTPS 协议混淆等问题。
---

# Ubuntu / Debian VPS：3x-ui 面板自动部署、备份与故障救援

## 适用环境与前置要求

### 服务器系统

- **已验证目标：Ubuntu 22.04 / Debian 11 或更新版本**，使用 systemd，具备公网网络访问。
- 当前备份脚本在缺少 SQLite CLI 时会调用 `apt-get` 安装，因此其他发行版即使被 3x-ui 上游支持，也不应宣称已由本 skill 完整兼容。
- 默认以 `root` 连接；如果使用普通用户，必须具备无交互 sudo 权限，并相应修改脚本中的 `/root` 路径和特权命令。
- 暂不支持 Windows VPS、容器内安装、OpenVZ 特殊模板或没有 systemd 的精简系统。

### 执行端

- Python 3.10 或更新版本。
- 安装 `requirements.txt` 中的依赖，目前主要是 `paramiko>=3.4.0`。
- 能访问目标 VPS 的 SSH 端口以及 GitHub Raw；若网络受限，预先下载官方安装器并使用 `--script`。

### 网络与证书

- 确认真实 SSH 端口；它不一定是 22。
- 面板端口不能与 SSH 或现有服务冲突。若需要公网访问，还要在云安全组和主机防火墙中放行。
- `ssl-mode=domain` 要求域名已解析到该 VPS，并且 ACME 所需端口可达。
- `ssl-mode=none` 只适合临时部署或配合受限的 SSH/VPN 隧道；不要长期通过公网 HTTP 登录。

### 开始前必须确认

1. 核对 VPS IP、SSH 端口和服务商控制台显示的 SSH 主机指纹。
2. 重装或迁移前先取得 `/etc/x-ui/x-ui.db` 的一致性备份。
3. 明确本次是全新安装、保留数据升级，还是已删除服务器后的重新安装。
4. 不要把 SSH 密码、面板密码、API Token、数据库或完整凭据文件上传到仓库或公开聊天。
5. 先在临时 VPS 上验证目标版本；不要未经测试就在生产服务器执行。

你是一名运维工程师。目标不是"跑完命令"，而是**让用户拿到一个能登录、能加节点、且配置已备份的面板**，并且把凭据整理成他能直接找到的文件。

## 先分清三件事

开工前必须先定性，否则会白干一轮。

### ① 是全新安装、重装，还是"续费后想恢复原面板"

| 场景 | 判断依据 | 结果 |
| --- | --- | --- |
| 全新安装 | 从没装过，或 `/usr/local/x-ui` 不存在 | 正常装 |
| 重装 | 机器还在，只是要换版本/重来 | **先备份 `/etc/x-ui/x-ui.db`** 再装 |
| 续费后想恢复 | 用户以为续费能拿回原来的面板 | 见下方「服务商后台状态」 |

### ② 服务商后台状态：暂停 ≠ 已终止

这是最容易误判的一环。用户说"服务器过期了，续费还能用原来的后台吗"，答案完全取决于后台状态：

| WHMCS 后台状态 | 含义 | 数据 |
| --- | --- | --- |
| 已暂停 / Suspended / 欠费停机 | 机器停机，磁盘保留 | ✅ 续费原样恢复，IP、端口、路径都不变 |
| **已终止 / Terminated / 已释放** | 服务器已删除，磁盘清空，IP 回收 | ❌ **续费也拿不回来**，只能重新开通 + 重装 |
| 后台找不到这台机器 | 已删除 | ❌ 同上 |

宽限期通常只有 3~7 天。**看到"已终止"就直接告诉用户救不回来，别让他先续费再发现白花钱。**

### ③ 面板是 HTTP 还是 HTTPS

不要凭经验假设。用户报"打不开"时，先实测协议：

```python
# 明文 GET 有响应 → 服务端说的是 HTTP
# TLS 握手报 WRONG_VERSION_NUMBER → 确认是 HTTP
```

浏览器报 `ERR_SSL_PROTOCOL_ERROR` 的常见原因之一是：面板跑 HTTP，但浏览器把 `http://` 自动升级成 `https://`。先用明文 GET 与 TLS 握手实测协议；短期诊断可明确输入完整 `http://` 地址，长期应改用可信 HTTPS 或仅通过 SSH/VPN 隧道访问，不要把关闭浏览器安全功能作为常规方案。

## 执行流程

### 第 1 步：探测（不要跳过）

```bash
python scripts/probe.py --host 1.2.3.4 --port 22 --user root
```

脚本输出：TCP 是否通、SSH banner、**服务器允许的认证方式**（`password` / `publickey`）、系统版本、是否已装 x-ui。

要点：

- **SSH 端口不一定是 22**。很多面板商给的是随机高位端口（如 61375）。22 不通不代表机器挂了。
- `auth_none` 能拿到 `allowed_types`，这一步能直接判断"是不是只允许密钥登录"。
- 如果 TCP 通但 SSH 拒绝连接，机器可能正在重启，等 30~60 秒重试。

### 第 2 步：拿到能用的密码

**这是实战中卡住最多的一步。** 面板商后台显示的「实例密码」经常**显示不全**（只显示前几位），截图读出来的密码必然认证失败。

排查顺序：

1. 用后台显示的密码试 → 失败
2. 试 `l/I/1`、`0/O` 等易混字符变体 → **最多试 3~4 个就停**，不要暴力枚举（会触发 fail2ban 或商家风控）
3. 全部失败 → **让用户点后台的「重置密码」**，并且：
   - 弹窗里那串是**系统生成的**，照样可能显示不全 → 让用户**选中文字复制**，粘贴成文本发过来（截图不算）
   - 最好让用户**自己输入一个纯字母数字的密码**（如 `Root2026abc`），彻底消除歧义
4. **重置密码必须重启才生效**。后台一般写着"重启后生效"，这类机器的密码是开机时由 cloud-init 写入的。不重启，SSH 认的还是旧密码。

### 第 3 步：非交互安装

```bash
python scripts/install.py --host 1.2.3.4 --port 22 --user root \
    --panel-port 41253 --ssl-mode none --accept-new-host-key
```

不传 `--password` 时脚本会在终端安全提示输入；自动化环境可通过 `VPS_SSH_PASSWORD` 注入。首次使用 `--accept-new-host-key` 前必须从服务商控制台核对 SSH 指纹。

脚本做的事：上传 `install.sh` → 用环境变量驱动安装（**零交互**）→ 解析 `/etc/x-ui/install-result.env` → 打印凭据 JSON。

关键在环境变量。v3.11 的 `install.sh` 支持非交互模式（`XUI_NONINTERACTIVE=1`，或 stdin 不是 TTY 时自动触发），全部提示都能用环境变量覆盖：

| 环境变量 | 作用 | 示例 |
| --- | --- | --- |
| `XUI_NONINTERACTIVE` | 强制非交互 | `1` |
| `XUI_USERNAME` | 面板用户名 | `u9QmK2vT7x` |
| `XUI_PASSWORD` | 面板密码 | 12 位随机 |
| `XUI_PANEL_PORT` | 面板端口 | 避开 SSH 端口 |
| `XUI_WEB_BASE_PATH` | 访问路径（随机串） | 18 位随机 |
| `XUI_SSL_MODE` | `none` / `domain` / `ip` | `none` |
| `XUI_DB_TYPE` | `sqlite` / `postgres` | `sqlite` |
| `XUI_SERVER_IP` | 用于拼 Access URL | 公网 IP |

细节见 [references/install-noninteractive.md](references/install-noninteractive.md)。

### 第 4 步：验收（三项都要过）

| 检查 | 命令 | 通过标准 |
| --- | --- | --- |
| 登录页可达 | 服务器本机请求面板 URL | `HTTP 200` + 页面含 `Sign in`；若设计为公网访问，再额外测公网 URL |
| 服务在跑 | `systemctl is-active x-ui` | `active` |
| 开机自启 | `systemctl is-enabled x-ui` | `enabled` |

三项缺一不可。只测 `systemctl` 不够——服务活着但端口没开（防火墙/监听地址错）照样打不开。

### 第 5 步：立刻备份数据库

```bash
python scripts/backup_db.py --host 1.2.3.4 --port 22 --user root --out ./backup
```

**这一步不能省。** `/etc/x-ui/x-ui.db` 里装着端口、路径、账号、以及**全部节点配置**。服务器一旦被删或重装，没有这个文件就是全部重来。

备份脚本使用 SQLite 在线备份 API并运行完整性检查，同时在服务器上留下带时间戳的副本；不要直接复制正在写入的数据库，也不要反复覆盖唯一的备份。

> 空库备份只有几百 KB 且没有节点。告诉用户：**加完节点后再备份一次**，那次才是真正有价值的。

### 第 6 步：凭据落盘

用 [references/credential-template.txt](references/credential-template.txt) 生成仅当前用户可读的凭据文件，并把文件路径交给用户。默认不要把密码、SSH 凭据或 API Token 贴进对话、日志或公开分享；只有用户明确要求查看时才显示秘密。

**不要覆盖已有文件。** 如果目录里已有同名凭据文件，另存为带日期的新文件。

## 故障对照表

| 现象 | 最可能的原因 | 处理 |
| --- | --- | --- |
| `ERR_SSL_PROTOCOL_ERROR` | 可能是面板跑 HTTP、浏览器强升 HTTPS | 实测协议；临时写全 `http://`，长期配置 HTTPS 或隧道 |
| 面板地址 404 | 漏了 WebBasePath 后缀 | 补上 `/xxxxxxxx` 随机路径 |
| 面板地址 404（路径对） | 端口填错，或面板没起来 | `ss -tlnp \| grep x-ui` |
| SSH 认证失败 | 密码显示不全 / 改了没重启 | 重置密码 + **重启** + 复制文字 |
| SSH 连接被拒 | 机器重启中 | 等 60 秒重试 |
| 22 端口不通 | SSH 不在 22 | 看后台给的端口 |
| 续费后地址失效 | 后台「已终止」，机器已删 | 救不回来，重装；新 IP/端口/路径全变 |
| 面板打开但英文 | 语言存在浏览器 cookie `lang` | 控制台执行 `document.cookie="lang=zh-CN; path=/; max-age=31536000"` 后刷新 |
| 延迟比标称高很多 | 路由没走优化线路 | `mtr` 测回程，找服务商 |

更多见 [references/troubleshoot.md](references/troubleshoot.md)。

## 安全边界

1. **秘密默认不进命令行、日志或对话。** SSH 密码优先从安全输入或环境变量读取；面板密码/API Token 写入权限受限文件，只有显式 `--show-secrets` 才显示。
2. **默认装 HTTP 面板时必须提醒用户**：密码明文过公网。SSH 隧道只加密访问链路，不会自动关闭公网入口；还要将面板绑定到本机或用防火墙限制来源。长期优先可信 HTTPS。
3. **重装前必问一句**：`/etc/x-ui/x-ui.db` 备份了吗？没有就先备份。
4. **不覆盖用户已有文件**，凭据另存带日期的新文件。
5. **不替用户加节点、不碰他的业务配置**，装完交给用户。
6. **首次连接必须核对主机指纹。** 默认拒绝未知主机密钥；确认指纹后才使用 `--accept-new-host-key` 保存它。
7. **安装器固定版本。** 默认使用经过验证的 tag；升级版本时先测试，可额外提供 SHA256 校验，不能静默跟随 `master`。

## 参考

- 安装脚本：`https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh`
- 上游仓库：`MHSanaei/3x-ui`
- 依赖：`paramiko`（`pip install -r requirements.txt`）
