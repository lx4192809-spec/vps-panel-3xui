# 3x-ui 故障排查手册

按"用户报的现象"查，不按技术分类查。

---

## 一、面板打不开

### 1.1 浏览器报 `ERR_SSL_PROTOCOL_ERROR` / 「无法提供安全连接」

**最可能：面板跑的是 HTTP，浏览器把它升级成 HTTPS 了。**

Chrome / Edge 默认开启「始终使用安全连接」（HTTPS-First），对裸 IP 地址会先尝试 `https://`。而 3x-ui 用 `XUI_SSL_MODE=none` 装出来的是纯 HTTP，TLS 握手必然失败。

实测确认方法（两个结果互斥）：

```python
# 明文 GET 有响应 → 服务端说 HTTP
sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
# 返回 b'HTTP/1.1 200 OK...'  → HTTP

# TLS 握手 → 报 WRONG_VERSION_NUMBER
# 说明对面根本不是 TLS 服务
```

**解法：**

1. 地址写全：`http://IP:端口/WebBasePath`
2. 短期诊断时明确输入完整 `http://` 地址；不要把关闭浏览器安全功能作为长期方案
3. 长期配置可信 HTTPS，或改用 SSH/VPN 隧道并关闭/限制公网面板端口

### 1.2 打开是 404

**A. 漏了 WebBasePath**

3x-ui 的访问路径是 `http://IP:端口/<18位随机串>`。只填 `http://IP:端口` 就是 404。

去 `/etc/x-ui/install-result.env` 里找 `XUI_WEB_BASE_PATH`，或在 SSH 里跑 `x-ui settings`。

**B. 路径对但端口错**

```bash
ss -tlnp | grep x-ui
```

看 x-ui 实际监听在哪个端口。

**C. 面板没起来**

```bash
systemctl status x-ui
x-ui log
```

### 1.3 端口连不上（TCP 都不通）

按顺序排除：

1. **机器在重启** —— 等 60 秒。重启期间 TCP 会被拒绝（`Connection refused`），不是超时。
2. **防火墙 / 安全组** —— 面板商后台通常有「安全组」页，要放行面板端口。3x-ui 装的时候不会自动改云厂商安全组。
3. **面板崩了** —— `systemctl restart x-ui`。
4. **IP 变了** —— 续费/换机后 IP 可能变，用 ping 确认。

---

## 二、SSH 登不上

### 2.1 认证失败（Authentication failed）

**按这个顺序排，别一上来就怀疑密码打错。**

1. **确认服务器允许密码登录**
   ```python
   transport.auth_none("root")   # 抛 BadAuthenticationType
   # e.allowed_types == ['password'] → 支持密码
   # e.allowed_types == ['publickey'] → 只能密钥，密码永远不对
   ```

2. **面板商显示的「实例密码」显示不全**
   这是最常见的坑。WHMCS 类后台的密码字段经常被截断，截图读出来的必然错。
   - 让用户**选中文字复制**，粘贴成纯文本发过来
   - 更彻底：让用户在「重置密码」里**自己输入**一个纯字母数字密码（如 `Root2026abc`），消除 `l/I/1`、`0/O` 歧义

3. **试变体要有节制**
   最多试 3~4 个（`l`↔`1`、`I`↔`1`、大小写），**不要暴力枚举**——会触发 fail2ban 或商家风控，把 IP 拉黑反而更难查。

4. **重置密码后必须重启**
   这类面板的密码是开机时由 cloud-init 写入 `/etc/shadow` 的。后台写着"重启后生效"就是字面意思：不重启，SSH 认的还是旧密码。

### 2.2 22 端口不通

**SSH 不一定在 22。** 面板商常给随机高位端口（实测见过 61375）。去后台「产品详情」看「端口」那一栏。

### 2.3 Connection refused vs 超时

| 现象 | 含义 |
| --- | --- |
| `Connection refused`（立即拒绝） | 机器活着，但该端口没服务 → 重启中，或端口填错 |
| 超时（无响应） | 机器挂了 / 被墙 / 安全组丢弃 |

---

## 三、续费 / 过期相关

### 3.1 「续费还能用原来的后台吗？」

**只看服务商后台的实例状态，别猜。**

| 状态 | 数据 | 结果 |
| --- | --- | --- |
| 已暂停 / Suspended / 停机 | 磁盘保留 | ✅ 续费原样恢复，IP、端口、路径全不变 |
| **已终止 / Terminated / 已释放** | 磁盘清空 | ❌ 拿不回来，续费=新开一台空机 |
| 后台找不到该实例 | 已删除 | ❌ 同上 |

部分服务商宽限期可能只有几天，但没有统一期限。看到「已终止」时先核对服务商的数据保留、快照和备份政策；通常无法从原磁盘恢复，不要承诺续费一定能找回。

### 3.2 续费后发现是全新机器，怎么判断

对比这几项，任意一项不同就说明数据没迁移：

| 项 | 旧机 | 新机 |
| --- | --- | --- |
| IP | 192.220.58.215 | 192.220.46.220 |
| SSH 端口 | 22 | 61375 |
| 旧面板端口 | 54712 通 | 54712 不通 |

再用旧面板的**完整 URL**（含 WebBasePath）探一次，返回 000 就确认是空机：

```bash
curl -s -o /dev/null -w "%{http_code}" "http://新IP:旧端口/旧路径/"
```

### 3.3 防止再发生

- 加完节点立刻备份 `/etc/x-ui/x-ui.db`
- 到期前 3 天续费，别卡最后一天

---

## 四、界面相关

### 4.1 界面是英文，怎么切中文

3x-ui v3 前端用 i18next，语言存在**浏览器 cookie `lang`** 里，不是存在服务器。

支持的语言值：`ar-EG` `en-US` `fa-IR` **`zh-CN`** **`zh-TW`** `ja-JP` `ru-RU` `vi-VN` `es-ES` `id-ID` `uk-UA` `tr-TR` `pt-BR` `sk-SK`

三种切法：

1. **界面**：登录页工具栏有语言下拉；已登录界面在设置区
2. **控制台**（最稳）：F12 → Console →
   ```javascript
   document.cookie = "lang=zh-CN; path=/; max-age=31536000"
   ```
   然后 F5
3. 换浏览器/清 cookie 会回退英文，需要重设

### 4.2 新旧界面差异

| | 旧版（v2.x） | 新版（v3.x） |
| --- | --- | --- |
| 风格 | Material | 深色现代风 |
| 菜单 | 入站/设置 | 多了 `Nodes`（多节点）、`Hosts` |
| 安装 | 纯交互 | 支持 `XUI_*` 环境变量 |

**不要按旧版印象找菜单。**

---

## 五、性能 / 线路

### 5.1 延迟比标称高很多

标称「CN2 GIA」「9929」的机器，国内 ping 正常在 **150~180ms**（美西）。实测 **240ms+** 说明：

- 路由没走优化线路（去程/回程绕路）
- 或者线路是虚标的

测法：

```bash
# 服务器上装 mtr
apt install -y mtr-tiny
mtr -rwzbc 100 <用户IP>
```

看回程是否经过 `cn2`（59.43.x.x）、`9929`（218.30.x / 219.158.x 之类）的骨干节点。

### 5.2 带宽跑不满

3x-ui 装完默认不开 BBR。开一下：

```bash
echo "net.core.default_qdisc=fq" >> /etc/sysctl.conf
echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.conf
sysctl -p
sysctl net.ipv4.tcp_congestion_control   # 应输出 bbr
```

---

## 六、安全检查清单

装完默认是 HTTP 面板时，必须跟用户说清楚：

1. **密码明文过公网** —— 任何一跳都能截
2. **公网高位端口会被扫** —— 18 位随机路径只是"猜不到"，不是"打不开"

两个补法：

| 方案 | 做法 | 适用 |
| --- | --- | --- |
| SSH 隧道 | `ssh -L 41253:127.0.0.1:41253 root@IP -p 端口`，访问 `http://127.0.0.1:41253/路径` | 最稳，端口被封也能进 |
| 恢复 HTTPS | 面板设置里配证书，或重装时 `XUI_SSL_MODE=domain` | 有域名时首选 |

若启用 fail2ban，先记录解封方式、管理 IP 白名单和服务商控制台救援入口。不要依赖未被当前上游安装器支持的环境变量来关闭它。
