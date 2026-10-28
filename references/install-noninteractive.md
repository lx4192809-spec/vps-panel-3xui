# 3x-ui 非交互安装详解

适用于 `MHSanaei/3x-ui` 的 `install.sh`（v3.x，实测 v3.11.0）。

## 为什么需要非交互

`install.sh` 默认是一串 `read -rp` 提示：数据库类型、面板端口、SSL 方式、证书路径……AI 或 CI 跑的时候没人敲回车，会一直挂在那里。

老教程让你 `printf '\n\n\n\n' | bash` 喂回车，这条路在 v3.x 上不可靠，因为脚本会**重新读 stdin**，喂进去的换行数量对不上就会错位。

v3.x 提供了正规做法。

## 触发条件

脚本顶部：

```bash
if [[ "${XUI_NONINTERACTIVE:-0}" == "1" ]] || [[ ! -t 0 ]]; then
    NONINTERACTIVE=1
else
    NONINTERACTIVE=0
fi
```

也就是说 **stdin 不是终端时自动进入非交互**。远程通过 `exec_command` 执行、`curl | bash`、cloud-init 都属于这种情况。

但**显式设置 `XUI_NONINTERACTIVE=1` 更稳**——避免脚本因为拿到伪终端而重新变成交互模式。

## 环境变量全表

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `XUI_NONINTERACTIVE` | `0` | 设 `1` 强制非交互 |
| `XUI_USERNAME` | 随机 10 位 | 面板用户名 |
| `XUI_PASSWORD` | 随机 10 位 | 面板密码 |
| `XUI_PANEL_PORT` | `shuf -i 1024-62000` | 面板端口；不设就随机 |
| `XUI_WEB_BASE_PATH` | 随机 18 位 | 访问路径后缀，**不能少于 4 位**，否则会重新生成 |
| `XUI_SSL_MODE` | `none` | `domain` / `ip` / `none`；未知值会降级为 `none` |
| `XUI_DOMAIN` | 空 | `SSL_MODE=domain` 时必填 |
| `XUI_ACME_EMAIL` | 空 | acme.sh 注册邮箱 |
| `XUI_ACME_HTTP_PORT` | `80` | HTTP-01 验证端口 |
| `XUI_SSL_IPV6` | 空 | 可选 IPv6 地址 |
| `XUI_DB_TYPE` | `sqlite` | `sqlite` / `postgres` |
| `XUI_DB_DSN` | 空 | postgres 连接串 |
| `XUI_SERVER_IP` | 自动探测 | 只用于拼显示用的 Access URL，不影响监听 |
| `XUI_MAIN_FOLDER` | `/usr/local/x-ui` | 安装目录 |
| `XUI_SERVICE` | `/etc/systemd/system` | systemd unit 目录 |

## SSL 模式怎么选

| 模式 | 结果 | 前提 | 建议 |
| --- | --- | --- | --- |
| `domain` | Let's Encrypt 域名证书，90 天自动续 | 有域名且已解析到本机；**80 端口开放** | 有域名就选这个，最稳 |
| `ip` | Let's Encrypt **IP 证书**，6 天自动续 | 80 端口开放 | 能用但证书短命，续期依赖 acme.sh 正常 |
| `none` | 纯 HTTP | 无 | 没域名时的默认；**必须提醒用户明文风险** |

`none` 模式下脚本会打印一段警告，这是预期的，不是报错：

```
⚠ Panel will be installed WITHOUT SSL/TLS.
Login credentials and cookies will travel as plain HTTP.
```

## 完整命令

```bash
export DEBIAN_FRONTEND=noninteractive
XUI_NONINTERACTIVE=1 \
XUI_SSL_MODE=none \
XUI_DB_TYPE=sqlite \
XUI_PANEL_PORT=41253 \
XUI_WEB_BASE_PATH=8sHqT2vLm9Kd3RxPw7 \
XUI_USERNAME=u9QmK2vT7x \
XUI_PASSWORD='Xq7Rt2Lm9Bv4Wn' \
XUI_SERVER_IP=1.2.3.4 \
bash /root/xui_install.sh 2>&1
```

`DEBIAN_FRONTEND=noninteractive` 是给 `apt-get` 用的，防止它中途弹 tzdata 之类的交互对话框卡住。

## 端口怎么选

- **避开 SSH 端口**（面板商常给 20000+ 的随机端口）
- 避开 80 / 443（可能留给反代或 ACME）
- 落在 `10000–62000` 之间，减少和系统服务撞车的概率
- 装之前先确认端口空闲：

```bash
ss -tlnp | grep -w 41253 || echo "端口空闲"
```

## 结果怎么拿

安装结束会同时输出到终端和写入文件：

- **终端**：`Username / Password / Port / WebBasePath / Database / Access URL / API Token`
- **文件**：`/etc/x-ui/install-result.env`（权限 `600`，root only）

文件格式可直接 `source`：

```
XUI_USERNAME=u9QmK2vT7x
XUI_PASSWORD=Xq7Rt2Lm9Bv4Wn
XUI_PANEL_PORT=41253
XUI_WEB_BASE_PATH=8sHqT2vLm9Kd3RxPw7
XUI_ACCESS_URL=http://1.2.3.4:41253/8sHqT2vLm9Kd3RxPw7
XUI_API_TOKEN=xxxxxxxx
XUI_DB_TYPE=sqlite
```

**解析这个文件比正则匹配终端输出可靠得多**（终端里有大量 curl 进度条和 ANSI 色码）。

## 安装后的目录

| 路径 | 内容 |
| --- | --- |
| `/usr/local/x-ui/` | 主程序 `x-ui`、CLI 脚本 `x-ui.sh` |
| `/usr/local/x-ui/bin/` | `xray-linux-amd64`、`geoip.dat`、`geosite.dat` 等 |
| `/etc/x-ui/x-ui.db` | **SQLite 数据库，端口/路径/账号/全部节点都在这里** |
| `/etc/systemd/system/x-ui.service` | systemd unit |
| `/etc/x-ui/install-result.env` | 安装结果凭据（600） |

## 常用运维命令

```bash
x-ui status      # 运行状态
x-ui restart     # 重启面板
x-ui settings    # 查看当前端口/路径等配置
x-ui log         # 日志
x-ui update      # 升级
x-ui uninstall   # 卸载
```

注意：`x-ui -v` 会打印帮助菜单而不是版本号，别指望它。
