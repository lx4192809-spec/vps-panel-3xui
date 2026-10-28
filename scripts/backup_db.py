#!/usr/bin/env python3
"""把服务器上的 /etc/x-ui/x-ui.db 拉到本地，并检查里面有多少节点。

这个文件装着面板的端口、路径、账号，以及**全部节点配置**。
服务器一旦被删或重装，没有它就是全部重来。

用法:
    python backup_db.py --host 1.2.3.4 --port 61375
    python backup_db.py --host 1.2.3.4 --out ./backup --label us-lax
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys
import time

try:
    import paramiko
except ImportError:
    sys.exit("缺少依赖：pip install paramiko")


def inspect(db_path: str) -> dict:
    """检查备份内容：是否可读、有多少入站节点。"""
    info = {"readable": False, "inbounds": 0, "users": 0}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("select count(*) from inbounds")
        info["inbounds"] = cur.fetchone()[0]
        try:
            cur.execute("select count(*) from client_traffics")
            info["users"] = cur.fetchone()[0]
        except sqlite3.Error:
            pass
        conn.close()
        info["readable"] = True
    except Exception:
        pass
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="备份 3x-ui 数据库")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="", help="不推荐：会出现在进程参数中")
    ap.add_argument("--password-env", default="VPS_SSH_PASSWORD")
    ap.add_argument("--accept-new-host-key", action="store_true")
    ap.add_argument("--out", default="./backup")
    ap.add_argument("--label", default="", help="文件名标记，如机房名")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    password = args.password or os.environ.get(args.password_env, "") or getpass.getpass("SSH 密码: ")
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
            password=password,
            timeout=args.timeout,
            banner_timeout=args.timeout,
            auth_timeout=args.timeout,
        )
    except paramiko.AuthenticationException:
        sys.exit("认证失败：密码不对")
    except Exception as exc:
        sys.exit(f"连接失败：{exc!r}")
    if args.accept_new_host_key:
        known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
        client.save_host_keys(known_hosts)

    # 使用 SQLite 在线备份 API，避免直接复制正在写入的数据库。
    remote_stamp = time.strftime("%Y%m%d-%H%M%S")
    remote_backup = f"/root/x-ui-{remote_stamp}.db.bak"
    backup_cmd = (
        "command -v sqlite3 >/dev/null 2>&1 || "
        "(apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sqlite3); "
        f"sqlite3 /etc/x-ui/x-ui.db \".backup '{remote_backup}'\" && "
        f"sqlite3 '{remote_backup}' 'PRAGMA integrity_check;'"
    )
    _, out, err = client.exec_command(backup_cmd, timeout=120)
    backup_output = out.read().decode(errors="replace").strip()
    backup_error = err.read().decode(errors="replace").strip()
    if out.channel.recv_exit_status() != 0 or "ok" not in backup_output.lower():
        client.close()
        sys.exit(f"服务器备份失败: {backup_error or backup_output}")
    print("服务器备份:", remote_backup)

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = f"-{args.label}" if args.label else ""
    dst = os.path.abspath(os.path.join(args.out, f"x-ui-{args.host}{tag}-{stamp}.db"))

    sftp = client.open_sftp()
    try:
        sftp.get(remote_backup, dst)
    finally:
        sftp.close()
        client.close()

    size = os.path.getsize(dst)
    info = inspect(dst)
    print(f"本地备份: {dst}  ({size} 字节)")
    if info["readable"]:
        print(f"内容检查: {info['inbounds']} 个入站节点, {info['users']} 个客户端")
        if info["inbounds"] == 0:
            print("  ⚠ 空库（还没加节点）。加完节点后请再备份一次，那次才有价值")
    else:
        print("  ⚠ 文件不是有效的 SQLite，可能下载不完整")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
