# Ubuntu / Debian VPS 3x-ui 自动部署、备份与故障救援

> 给 AI Agent 用的 3x-ui 面板部署技能：SSH 探测 → 非交互安装 → 验收 → 备份数据库 → 凭据落盘，一次做完。

一个 [Agent Skill](https://www.workbuddy.cn/docs/workbuddy/Overview)，面向 [3x-ui](https://github.com/MHSanaei/3x-ui) 面板的**部署与救援**。

## 适用系统

| 项目 | 要求 |
| --- | --- |
| VPS 系统 | **Ubuntu 22.04 / Debian 11 或更新版本**；systemd；公网网络正常 |
| 执行端 | Python 3.10+，安装 `requirements.txt` |
| SSH 权限 | 默认 root；普通用户需无交互 sudo，并修改脚本中的特权路径 |
| 网络 | 执行端能连接 VPS 的真实 SSH 端口；VPS 能访问 GitHub Raw 或使用本地 `--script` |
| HTTPS 域名模式 | 域名已解析至 VPS，ACME 验证端口可达 |

3x-ui 上游还支持其他 Linux 发行版，但本项目的数据库备份脚本在缺少 SQLite CLI 时使用 `apt-get`，所以目前只将 Ubuntu/Debian 列为完整支持。暂不支持 Windows VPS、容器内安装及没有 systemd 的精简系统。

## 上传或执行前注意

- **先删除真实凭据和备份**：不得上传 SSH 密码、面板密码、API Token、完整面板 URL、`.db`、日志或凭据 JSON。
- **先核对 SSH 指纹**：默认拒绝未知主机密钥；确认无误后才使用 `--accept-new-host-key`。
- **重装前先备份**：必须先备份 `/etc/x-ui/x-ui.db`，并通过完整性检查。
- **HTTP 不是长期方案**：`ssl-mode=none` 仅用于临时部署或受限隧道；SSH 隧道不会自动关闭公网面板端口。
- **先用临时 VPS 测试**：默认固定经过测试的安装器版本，升级前重新验证，不要静默跟随 `master`。
- **确认端口策略**：公网面板需要同时配置云安全组和主机防火墙；仅隧道访问时应关闭或限制公网入口。

## 为什么又造一个

GitHub 上已有的 VPS 代理类 skill，几乎都绕开面板，用 `v2ray-agent` 或手搓 `sing-box`。但对习惯在面板里管理节点的用户来说，那套不合适。

更关键的是：**3x-ui v3.x 支持全自动非交互安装**，靠一组 `XUI_*` 环境变量，能做到零交互。而网上的教程还全是"一路回车"的交互式流程，AI 照着做会直接卡死在第一个 `read -rp`。

这个 skill 把这件事固化了，另外补上了三个只有实战才会踩到的坑。

## 三个坑

| 坑 | 后果 | 本 skill 的处理 |
| --- | --- | --- |
| 面板商后台的「实例密码」显示不全 | 截图读出来的密码必然认证失败，来回试到怀疑人生 | 先探测认证方式 → 试 3~4 个易混变体就停 → 让用户自己设一个纯字母数字密码并**重启** |
| 重置密码必须重启才生效 | 改完密码还是登不上 | 明确写入流程：`确定` → `重启` → 等 `运行中` |
| 面板跑 HTTP，浏览器强升 HTTPS | `ERR_SSL_PROTOCOL_ERROR`，看着像证书问题其实是协议错 | 用明文 GET + TLS 握手双向实测判定，而不是靠猜 |

还有一个高频误判：**服务商后台的「已暂停」和「已终止」是两回事**。暂停 = 磁盘保留，续费原样恢复；已终止 = 机器已删，续费也拿不回来。宽限期通常只有 3~7 天。

## 安装

```bash
git clone https://github.com/<you>/vps-panel-3xui.git
```

放到你的 Agent 技能目录：

| 工具 | 路径 |
| --- | --- |
| WorkBuddy | `~/.workbuddy-ai/skills/vps-panel-3xui/` |
| Claude Code | `~/.claude/skills/vps-panel-3xui/` |
| Codex | `~/.codex/skills/vps-panel-3xui/` |

装依赖：

```bash
pip install -r requirements.txt
```

## 用法

### 1. 探测（不提供密码也能跑）

```bash
python scripts/probe.py --host 1.2.3.4 --port 61375
```

输出 TCP 可达性、SSH banner、**服务器允许的认证方式**、系统信息、是否已装 x-ui。

`22` 不通不代表机器挂了——面板商常给随机高位端口。

### 2. 安装

```bash
python scripts/install.py \
    --host 1.2.3.4 \
    --port 61375 \
    --panel-port 41253 \
    --ssl-mode none \
    --accept-new-host-key
```

不传 `--password` 时会安全提示输入；自动化环境使用 `VPS_SSH_PASSWORD`，避免把密码留在命令历史和进程列表。首次接受主机密钥前，先从服务商控制台核对 SSH 指纹。

自动完成：检查端口占用 → 上传固定版本 `install.sh` → 环境变量驱动安装 → 解析 `/etc/x-ui/install-result.env` → 本机/外网验收 → 将凭据写入权限受限文件。终端默认隐藏密码和 API Token；只有明确使用 `--show-secrets` 才显示。

加 `--json` 只输出 JSON，方便程序消费。

### 3. 备份数据库

```bash
python scripts/backup_db.py --host 1.2.3.4 --port 61375 --out ./backup
```

通过 SQLite 在线备份 API生成一致性快照，执行完整性检查后拉回本地，并检查入站节点数量。服务器端副本使用时间戳，不覆盖唯一备份。

## 非交互安装的关键

`install.sh` 在 stdin 不是终端时会自动进入非交互模式，但显式设置更稳：

```bash
XUI_NONINTERACTIVE=1 \
XUI_SSL_MODE=none \
XUI_DB_TYPE=sqlite \
XUI_PANEL_PORT=41253 \
XUI_WEB_BASE_PATH=8sHqT2vLm9Kd3RxPw7 \
XUI_USERNAME=u9QmK2vT7x \
XUI_PASSWORD='Xq7Rt2Lm9Bv4Wn' \
XUI_SERVER_IP=1.2.3.4 \
bash /root/xui_install.sh
```

完整变量表见 [`references/install-noninteractive.md`](references/install-noninteractive.md)。

## 目录结构

```
vps-panel-3xui/
├── SKILL.md                            # 技能主文件：定性 → 流程 → 故障表 → 边界
├── references/
│   ├── install-noninteractive.md       # XUI_* 环境变量全表
│   ├── troubleshoot.md                 # 按现象查的故障手册
│   └── credential-template.txt         # 凭据落盘模板
├── scripts/
│   ├── probe.py                        # SSH 可达性 + 认证方式探测
│   ├── install.py                      # 非交互安装 + 验收
│   └── backup_db.py                    # 备份数据库 + 节点数检查
├── requirements.txt
└── README.md
```

## 安全说明

- 脚本默认不打印密码和 API Token，安装日志里的密码会被替换成 `***`
- `.gitignore` 已排除 `*.db`（数据库含节点私钥）、备份目录、凭据文件
- 默认拒绝未知 SSH 主机密钥；首次接受前必须核对指纹
- 默认固定经过测试的安装器版本，可选 SHA256 校验
- 默认装 HTTP 面板时会主动提醒明文风险；SSH 隧道之外还要关闭或限制公网面板入口
- 不覆盖用户已有文件，凭据另存带日期的新文件

## 兼容性

实测于 **x-ui v3.11.0** / Ubuntu 22.04。v2.x 不支持 `XUI_*` 环境变量，需要改回交互式流程。

## License

MIT

## 联系作者

如有使用问题、兼容性反馈或改进建议，可通过邮箱联系：

- Email: [lx4192809@gmail.com](mailto:lx4192809@gmail.com)
