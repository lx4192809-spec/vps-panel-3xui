#!/usr/bin/env python3
"""Safely install 3x-ui over SSH in unattended mode."""
from __future__ import annotations

import argparse, getpass, hashlib, json, os, re, secrets, shlex, stat, string, sys, time, urllib.request

try:
    import paramiko
except ImportError:
    sys.exit("缺少依赖：pip install paramiko")

DEFAULT_VERSION = "v3.11.0"
UPSTREAM = "https://raw.githubusercontent.com/MHSanaei/3x-ui/{version}/install.sh"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_PATH = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
SAFE_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")


def rand(n: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def require_match(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        sys.exit(f"{label} 含不支持的字符或格式错误")
    return value


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_script(local: str | None, version: str, expected_hash: str, refresh: bool) -> str:
    if local:
        if not os.path.isfile(local):
            sys.exit(f"脚本不存在: {local}")
        path = local
    else:
        require_match(version, SAFE_TOKEN, "版本")
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, f"install-{version}.sh")
        if refresh or not (os.path.isfile(path) and os.path.getsize(path) > 10000):
            url = UPSTREAM.format(version=version)
            print(f"[0] 下载固定版本安装器 {version} ...", end=" ", flush=True)
            try:
                with urllib.request.urlopen(url, timeout=40) as resp:
                    data = resp.read()
                if len(data) < 10000:
                    sys.exit("下载内容异常短，拒绝执行")
                tmp = path + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
            except Exception as exc:
                sys.exit(f"下载安装器失败: {exc!r}\n可手动下载后使用 --script")
            print(f"完成，SHA256={sha256_file(path)}")
    actual = sha256_file(path)
    if expected_hash and actual.lower() != expected_hash.lower():
        sys.exit(f"安装器 SHA256 不匹配：实际 {actual}")
    return path


def connect(args, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy() if args.accept_new_host_key else paramiko.RejectPolicy())
    client.connect(args.host, port=args.port, username=args.user, password=password,
                   timeout=args.timeout, banner_timeout=args.timeout, auth_timeout=args.timeout)
    if args.accept_new_host_key:
        known_hosts = os.path.expanduser("~/.ssh/known_hosts")
        os.makedirs(os.path.dirname(known_hosts), exist_ok=True)
        client.save_host_keys(known_hosts)
    return client


def run(client, cmd: str, timeout: int = 60) -> tuple[int, str]:
    _, out, err = client.exec_command(cmd, timeout=timeout)
    text = out.read().decode(errors="replace") + err.read().decode(errors="replace")
    return out.channel.recv_exit_status(), text


def read_result(client) -> dict[str, str]:
    _, text = run(client, "cat /etc/x-ui/install-result.env 2>/dev/null")
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("XUI_"):
            continue
        try:
            parsed = shlex.split(line, posix=True)
        except ValueError:
            continue
        if len(parsed) == 1 and "=" in parsed[0]:
            key, value = parsed[0].split("=", 1)
            result[key] = value
    return result


def write_secret_result(result: dict, requested_path: str) -> str:
    path = os.path.abspath(requested_path)
    if os.path.exists(path):
        base, ext = os.path.splitext(path)
        path = f"{base}-{time.strftime('%Y%m%d-%H%M%S')}{ext or '.json'}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="非交互安装 3x-ui 面板")
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="", help="不推荐：会出现在进程参数中")
    ap.add_argument("--password-env", default="VPS_SSH_PASSWORD")
    ap.add_argument("--accept-new-host-key", action="store_true")
    ap.add_argument("--panel-port", type=int, default=0)
    ap.add_argument("--web-base-path", default="")
    ap.add_argument("--xui-user", default="")
    ap.add_argument("--xui-pass", default="")
    ap.add_argument("--ssl-mode", default="none", choices=["none", "domain", "ip"])
    ap.add_argument("--domain", default="")
    ap.add_argument("--db", default="sqlite", choices=["sqlite", "postgres"])
    ap.add_argument("--db-dsn", default="")
    ap.add_argument("--script", default=None)
    ap.add_argument("--xui-version", default=DEFAULT_VERSION)
    ap.add_argument("--installer-sha256", default="")
    ap.add_argument("--refresh-installer", action="store_true")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--install-timeout", type=int, default=600)
    ap.add_argument("--credentials-out", default="./credentials/3x-ui.json")
    ap.add_argument("--show-secrets", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not 1 <= args.port <= 65535:
        sys.exit("SSH 端口超出范围")
    if args.ssl_mode == "domain" and not args.domain:
        sys.exit("--ssl-mode domain 必须配 --domain")
    if args.domain:
        require_match(args.domain, SAFE_DOMAIN, "域名")
    if args.db == "postgres" and not args.db_dsn:
        sys.exit("--db postgres 必须配 --db-dsn")
    panel_port = args.panel_port or secrets.randbelow(52000) + 10000
    if not 1024 <= panel_port <= 65535 or panel_port == args.port:
        sys.exit("面板端口无效或与 SSH 端口冲突")
    web_path = require_match(args.web_base_path or rand(18), SAFE_PATH, "WebBasePath")
    xui_user = require_match(args.xui_user or rand(10), SAFE_TOKEN, "面板用户名")
    xui_pass = require_match(args.xui_pass or rand(20), SAFE_TOKEN, "面板密码")
    password = args.password or os.environ.get(args.password_env, "") or getpass.getpass("SSH 密码: ")
    if not password:
        sys.exit("未提供 SSH 密码")

    script = fetch_script(args.script, args.xui_version, args.installer_sha256, args.refresh_installer)
    log = (lambda *a, **k: None) if args.json else print
    try:
        client = connect(args, password)
    except paramiko.ssh_exception.SSHException as exc:
        sys.exit(f"SSH 连接失败: {exc}. 首次连接请核对指纹后使用 --accept-new-host-key")
    except Exception as exc:
        sys.exit(f"连接失败: {exc!r}")

    _, ports = run(client, "ss -H -ltn 2>/dev/null | awk '{print $4}'")
    if re.search(rf":{panel_port}$", ports, re.MULTILINE):
        client.close(); sys.exit(f"端口 {panel_port} 已被占用")
    sftp = client.open_sftp()
    try:
        sftp.put(script, "/root/xui_install.sh")
        sftp.chmod("/root/xui_install.sh", 0o700)
    finally:
        sftp.close()

    values = {"XUI_NONINTERACTIVE": "1", "XUI_SSL_MODE": args.ssl_mode,
              "XUI_DB_TYPE": args.db, "XUI_PANEL_PORT": str(panel_port),
              "XUI_WEB_BASE_PATH": web_path, "XUI_USERNAME": xui_user,
              "XUI_PASSWORD": xui_pass, "XUI_SERVER_IP": args.host}
    if args.domain: values["XUI_DOMAIN"] = args.domain
    if args.db_dsn: values["XUI_DB_DSN"] = args.db_dsn
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in values.items())
    cmd = f"export DEBIAN_FRONTEND=noninteractive; {env} bash /root/xui_install.sh 2>&1"
    log("安装中……")
    chan = client.get_transport().open_session()
    chan.settimeout(2.0); chan.exec_command(cmd)
    buf = bytearray(); deadline = time.monotonic() + args.install_timeout
    while not (chan.exit_status_ready() and not chan.recv_ready()):
        while chan.recv_ready(): buf.extend(chan.recv(65536))
        if time.monotonic() >= deadline:
            chan.close(); client.close()
            sys.exit("安装超时；远程通道已关闭，请检查进程和日志后再重试")
        time.sleep(0.2)
    code = chan.recv_exit_status()
    if not args.json:
        safe = bytes(buf).decode(errors="replace").replace(xui_pass, "***")
        print("\n".join(safe.strip().splitlines()[-25:]))

    info = read_result(client)
    if not info.get("XUI_ACCESS_URL"):
        client.close(); sys.exit("未读到安装结果，安装可能失败")
    _, status = run(client, "systemctl is-active x-ui; systemctl is-enabled x-ui")
    checks = status.split()
    scheme = "https" if args.ssl_mode != "none" else "http"
    local_url = f"{scheme}://127.0.0.1:{info.get('XUI_PANEL_PORT', panel_port)}/{info.get('XUI_WEB_BASE_PATH', web_path)}/"
    curl_flag = "-k" if args.ssl_mode == "ip" else ""
    local_code, body = run(client, f"curl {curl_flag} -fsS --max-time 15 {shlex.quote(local_url)} | head -c 8192", 20)
    local_ok = local_code == 0 and "sign in" in body.lower()
    external_ok = False
    try:
        with urllib.request.urlopen(info["XUI_ACCESS_URL"] + "/", timeout=15) as resp:
            external_ok = resp.status == 200 and "sign in" in resp.read(8192).decode(errors="replace").lower()
    except Exception:
        pass

    result = {"host": args.host, "ssh_port": args.port, "ssh_user": args.user,
              "panel_url": info["XUI_ACCESS_URL"], "panel_port": info.get("XUI_PANEL_PORT"),
              "web_base_path": info.get("XUI_WEB_BASE_PATH"), "username": info.get("XUI_USERNAME"),
              "password": info.get("XUI_PASSWORD"), "api_token": info.get("XUI_API_TOKEN"),
              "db_type": info.get("XUI_DB_TYPE"), "ssl_mode": args.ssl_mode,
              "service_active": "active" in checks, "service_enabled": "enabled" in checks,
              "local_panel_ok": local_ok, "external_panel_ok": external_ok,
              "installer_version": args.xui_version, "installer_sha256": sha256_file(script)}
    credentials_path = write_secret_result(result, args.credentials_out)
    client.close()
    public = result if args.show_secrets else {**result, "password": "***", "api_token": "***"}
    public["credentials_file"] = credentials_path
    if args.json: print(json.dumps(public, ensure_ascii=False, indent=2))
    else:
        for key, value in public.items(): print(f"{key:22s}: {value}")
    return 0 if code == 0 and result["service_active"] and result["service_enabled"] and local_ok else 1


if __name__ == "__main__":
    sys.exit(main())
