#!/usr/bin/env python3
"""探测 VPS 的 SSH 可达性与认证方式。

不提供密码也能跑——只做 TCP + banner + 认证方式探测，
用来判断"机器是否活着""SSH 端口对不对""是不是只能密钥登录"。

用法:
    python probe.py --host 1.2.3.4 --port 61375
    # 提供 VPS_SSH_PASSWORD 后再执行登录探测；不要把密码写进命令行。
    python probe.py --host 1.2.3.4 --port 61375 --user root
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import socket
import sys

try:
    import paramiko
except ImportError:
    sys.exit("缺少依赖：pip install paramiko")


def tcp_probe(host: str, port: int, timeout: float = 8.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="探测 VPS SSH 可达性与认证方式")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default=None, help="不推荐：会出现在进程参数中")
    ap.add_argument("--password-env", default="VPS_SSH_PASSWORD")
    ap.add_argument("--accept-new-host-key", action="store_true")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    print(f"[1/4] TCP {args.host}:{args.port} ...", end=" ", flush=True)
    if not tcp_probe(args.host, args.port, timeout=8):
        print("不通")
        print("  → 机器可能已关机/重启中，或端口不对，或被安全组拦截")
        print("  → 22 不通不代表机器挂了：面板商常给随机高位端口")
        return 2
    print("通")

    print("[2/4] SSH 握手 ...", end=" ", flush=True)
    transport = paramiko.Transport((args.host, args.port))
    try:
        transport.start_client(timeout=args.timeout)
    except Exception as exc:
        print("失败")
        print(f"  → {exc!r}")
        return 3
    banner = (transport.get_banner() or b"").decode(errors="replace").strip()
    print("成功")
    server_key = transport.get_remote_server_key()
    fingerprint = base64.b64encode(hashlib.sha256(server_key.asbytes()).digest()).decode().rstrip("=")
    print(f"  主机密钥: {server_key.get_name()} SHA256:{fingerprint}")
    if banner:
        print(f"  banner: {banner.splitlines()[0]}")

    print("[3/4] 认证方式 ...", end=" ", flush=True)
    allowed: list[str] = []
    try:
        transport.auth_none(args.user)
        print("无需认证即可登录（异常！请检查配置）")
        allowed = ["none"]
    except paramiko.BadAuthenticationType as exc:
        allowed = list(exc.allowed_types)
        print(", ".join(allowed))
    except Exception as exc:
        print(f"探测失败 {exc!r}")
    transport.close()

    if "password" not in allowed and allowed:
        print("  ⚠ 服务器不允许密码登录，密码永远正确不了 —— 需要密钥或改 sshd 配置")

    supplied_password = args.password or os.environ.get(args.password_env)
    if not supplied_password:
        print("[4/4] 跳过登录（未提供密码）")
        return 0

    print("[4/4] 密码登录 ...", end=" ", flush=True)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(
        paramiko.AutoAddPolicy() if args.accept_new_host_key else paramiko.RejectPolicy()
    )
    try:
        client.connect(
            args.host,
            port=args.port,
            username=args.user,
            password=supplied_password,
            timeout=args.timeout,
            banner_timeout=args.timeout,
            auth_timeout=args.timeout,
        )
    except paramiko.AuthenticationException:
        print("失败")
        print("  → 密码不对。常见原因：后台显示的密码被截断、重置后未重启")
        print("  → 让用户在后台自己输入一个纯字母数字密码，重置后重启")
        return 4
    except Exception as exc:
        print("失败")
        print(f"  → {exc!r}")
        return 5
    if args.accept_new_host_key:
        known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
        client.save_host_keys(known_hosts)
    print("成功")

    cmds = {
        "主机名": "hostname",
        "系统": "grep PRETTY_NAME /etc/os-release | cut -d= -f2- | tr -d '\"'",
        "内核": "uname -r",
        "CPU 核数": "nproc",
        "内存": "free -m | awk 'NR==2{print $2\" MB\"}'",
        "磁盘": "df -h / | awk 'NR==2{print $4\" 可用 / \"$2}'",
        "已装 x-ui": "test -d /usr/local/x-ui && echo 已安装 || echo 未安装",
        "x-ui 服务": "systemctl is-active x-ui 2>/dev/null || echo 无",
        "监听端口": "ss -tlnp 2>/dev/null | grep x-ui || echo 无",
    }
    print("\n--- 服务器信息 ---")
    for label, cmd in cmds.items():
        _, out, _ = client.exec_command(cmd, timeout=20)
        val = out.read().decode(errors="replace").strip() or "-"
        print(f"{label:10s}: {val}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
