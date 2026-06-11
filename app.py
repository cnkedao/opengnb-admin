#!/usr/bin/env python3
"""
OpenGNB 管理端

功能: Web 管理 Index+Forward 节点配置、启停及内网节点配置包生成下载
日期: 2026-06-11
用法:
  python3 app.py
  python3 app.py --host 0.0.0.0 --port 8080
  GNB_BIN=/usr/local/bin/gnb python3 app.py
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import traceback
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ============================================================
# 配置区域
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONF_DIR = DATA_DIR / "conf"
CONFIG_FILE = DATA_DIR / "network.json"
PID_FILE = DATA_DIR / "index_forward.pid"
STATIC_DIR = BASE_DIR / "static"

GNB_DEFAULT_DETECT_INTERVAL = "5000,367"
GNB_DEFAULT_WORKER_QUEUE = "4095"

GNB_ADV_DEFAULTS = {
    "safe_index": "off",
    "crypto_key_update_interval": "",
    "multi_forward_type": "",
    "memory": "",
    "zip": "",
    "zip_level": "",
    "detect_interval": "",
    "node_worker_queue": "",
    "index_worker_queue": "",
    "index_service_worker_queue": "",
    "packet_filter_worker_queue": "",
    "pf_worker": "0",
    "pf_route_bits": "",
}

GNB_DEFAULT_LOG_PATH = "logs/gnb-{nodeid}.log"
GNB_DEFAULT_LOG_LEVEL = "3"
GNB_STDOUT_LOG_FILE = DATA_DIR / "logs" / "index_forward.log"
# OpenGNB 需同时设置 main/模块 与 file 级别，仅 file-log-level 不会写文件
GNB_LOG_CONF_KEYS = (
    "console-log-level",
    "file-log-level",
    "main-log-level",
    "core-log-level",
    "node-log-level",
    "index-log-level",
    "index-service-log-level",
)
GNB_LOG_CLI_FLAGS = (
    ("console-log-level", "--console-log-level"),
    ("file-log-level", "--file-log-level"),
    ("main-log-level", "--main-log-level"),
    ("core-log-level", "--core-log-level"),
    ("node-log-level", "--node-log-level"),
    ("index-log-level", "--index-log-level"),
    ("index-service-log-level", "--index-service-log-level"),
)

INDEX_NODE_DEFAULTS = {
    "enable_forward": True,
    "multi_socket": "off",
    "extra_listens": [],
    "index_worker": "on",
    "index_service_worker": "on",
    "node_detect_worker": "off",
    "set_fwdu0": "on",
    "direct_forwarding": "on",
    "es_argv": "",
    "unified_forwarding": "",
    "ip_stack": "both",
    "mtu": "",
    "gnb_log_enabled": False,
    "gnb_log_path": GNB_DEFAULT_LOG_PATH,
    **GNB_ADV_DEFAULTS,
}

CLIENT_NODE_DEFAULTS = {
    "platform": "linux",
    "if_drv": "wintun",
    "multi_socket": "on",
    "extra_listens": [],
    "node_detect_worker": "on",
    "set_fwdu0": "on",
    "direct_forwarding": "on",
    "es_argv": "--upnp",
    "discover_in_lan": False,
    "unified_forwarding": "auto",
    "ip_stack": "both",
    "mtu": "",
    **GNB_ADV_DEFAULTS,
}

DEFAULT_NETWORK = {
    "index_forward": {
        "nodeid": 1001,
        "listen": 9001,
        "public_ip": "",
        "passcode": "",
        "tun_ip": "10.1.0.1",
        "netmask": "255.255.255.0",
        **INDEX_NODE_DEFAULTS,
    },
    "clients": [
        {
            "nodeid": 1002,
            "listen": 9002,
            "tun_ip": "10.1.0.2",
            "lan_routes": [],
            **CLIENT_NODE_DEFAULTS,
        },
        {
            "nodeid": 1003,
            "listen": 9003,
            "tun_ip": "10.1.0.3",
            "lan_routes": [],
            **CLIENT_NODE_DEFAULTS,
        },
    ],
    "gnb_bin": os.environ.get("GNB_BIN", "/data2/opengnb-1.6.5/bin"),
    "gnb_crypto_bin": os.environ.get("GNB_CRYPTO_BIN", ""),
}

ADMIN_TOKEN = os.environ.get("GNB_ADMIN_TOKEN", "")

# 管理端配置项、高级选项与默认 bin 路径所对齐的 OpenGNB 版本
TARGET_GNB_VERSION = os.environ.get("GNB_TARGET_VERSION", "1.6.5")

# ============================================================
# 工具函数
# ============================================================


def _script_name() -> str:
    try:
        return os.path.abspath(__file__)
    except NameError:
        return "<stdin>"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def load_network() -> dict:
    ensure_dirs()
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = DEFAULT_NETWORK.copy()
        merged.update({k: v for k, v in data.items() if k in DEFAULT_NETWORK})
        if "index_forward" in data:
            merged["index_forward"] = {
                **DEFAULT_NETWORK["index_forward"],
                **INDEX_NODE_DEFAULTS,
                **data["index_forward"],
            }
        if "clients" in data:
            merged["clients"] = [
                {**CLIENT_NODE_DEFAULTS, **c} for c in data["clients"]
            ]
        return normalize_bin_setting(merged)
    return normalize_bin_setting(json.loads(json.dumps(DEFAULT_NETWORK)))


def save_network(data: dict) -> None:
    ensure_dirs()
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge_network(body: dict, base: dict | None = None) -> dict:
    network = json.loads(json.dumps(base or load_network()))
    if "index_forward" in body:
        network["index_forward"].update(body["index_forward"])
    if "clients" in body:
        network["clients"] = body["clients"]
    if "gnb_bin" in body:
        network["gnb_bin"] = body["gnb_bin"]
    if "gnb_crypto_bin" in body:
        network["gnb_crypto_bin"] = body["gnb_crypto_bin"]
    idx = network.get("index_forward", {})
    idx["public_ip"] = str(idx.get("public_ip") or "").strip()
    network["index_forward"] = idx
    return normalize_bin_setting(sanitize_lan_routes(network))


def sanitize_lan_routes(network: dict) -> dict:
    """仅保留用户填写的完整 LAN 路由。"""
    for client in network.get("clients", []):
        routes = []
        for r in client.get("lan_routes", []):
            network_addr = (r.get("network") or "").strip()
            netmask = (r.get("netmask") or "").strip()
            via_tun_ip = (r.get("via_tun_ip") or "").strip()
            if network_addr and netmask and via_tun_ip:
                routes.append(
                    {
                        "network": network_addr,
                        "netmask": netmask,
                        "via_tun_ip": via_tun_ip,
                    }
                )
        client["lan_routes"] = routes
    return network


def normalize_bin_setting(network: dict) -> dict:
    """支持填写 bin 目录（自动补全 gnb / gnb_crypto 可执行文件路径）。"""
    gnb_raw = (network.get("gnb_bin") or "gnb").strip()
    crypto_raw = (network.get("gnb_crypto_bin") or "gnb_crypto").strip()

    gnb_path = Path(gnb_raw)
    if gnb_path.is_dir():
        bin_dir = gnb_path.resolve()
        network["gnb_bin"] = str(bin_dir / "gnb")
        if crypto_raw in ("", "gnb_crypto") or Path(crypto_raw).is_dir():
            network["gnb_crypto_bin"] = str(bin_dir / "gnb_crypto")
        elif not Path(crypto_raw).is_file():
            network["gnb_crypto_bin"] = str(bin_dir / "gnb_crypto")
    elif gnb_path.is_file():
        network["gnb_bin"] = str(gnb_path.resolve())
        bin_dir = gnb_path.parent
        crypto_path = Path(crypto_raw)
        if crypto_raw in ("", "gnb_crypto") and not shutil.which(crypto_raw):
            candidate = bin_dir / "gnb_crypto"
            if candidate.is_file():
                network["gnb_crypto_bin"] = str(candidate)

    crypto_path = Path(network.get("gnb_crypto_bin", "gnb_crypto"))
    if crypto_path.is_dir():
        network["gnb_crypto_bin"] = str(crypto_path.resolve() / "gnb_crypto")

    return network


def resolve_binary(name: str, label: str, exe_name: str = "") -> str:
    raw = name.strip()
    path = Path(raw)
    if path.is_dir() and exe_name:
        path = path / exe_name
    if path.is_file():
        if not os.access(path, os.X_OK):
            raise PermissionError(f"{label} 不可执行: {path}")
        return str(path.resolve())
    if os.sep not in raw and not (os.altsep and os.altsep in raw):
        found = shutil.which(raw)
        if found:
            return found
    raise FileNotFoundError(
        f"找不到 {label}「{raw}」。"
        f"可填写 bin 目录（如 /data2/opengnb-1.6.5/bin）或完整路径（如 .../bin/gnb）"
    )


def tail_log(log_file: Path, lines: int = 20) -> str:
    if not log_file.exists():
        return ""
    text = log_file.read_text(encoding="utf-8", errors="replace")
    parts = text.strip().splitlines()
    return "\n".join(parts[-lines:])


def format_gnb_log_path(raw: str, nodeid: int) -> str:
    template = (raw or GNB_DEFAULT_LOG_PATH).strip()
    return template.replace("{nodeid}", str(nodeid))


def resolve_gnb_log_path(network: dict) -> Path:
    idx = network["index_forward"]
    text = format_gnb_log_path(
        idx.get("gnb_log_path") or GNB_DEFAULT_LOG_PATH,
        idx["nodeid"],
    )
    path = Path(text)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def gnb_log_status(network: dict) -> dict:
    idx = network["index_forward"]
    enabled = bool(idx.get("gnb_log_enabled"))
    configured = format_gnb_log_path(
        idx.get("gnb_log_path") or GNB_DEFAULT_LOG_PATH,
        idx["nodeid"],
    )
    if not enabled:
        return {
            "enabled": False,
            "configured_path": configured,
            "path": "",
            "exists": False,
        }
    path = resolve_gnb_log_path(network)
    return {
        "enabled": True,
        "configured_path": configured,
        "path": str(path),
        "exists": path.is_file(),
    }


def ensure_gnb_log_dir(network: dict) -> Path | None:
    if not network["index_forward"].get("gnb_log_enabled"):
        return None
    path = resolve_gnb_log_path(network)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_gnb_log_level_lines(lines: list[str], level: str = GNB_DEFAULT_LOG_LEVEL) -> None:
    for key in GNB_LOG_CONF_KEYS:
        lines.append(f"{key} {level}")


def build_gnb_log_cli(network: dict) -> list[str]:
    if not network["index_forward"].get("gnb_log_enabled"):
        return []
    ensure_gnb_log_dir(network)
    log_path = resolve_gnb_log_path(network)
    args = ["--log-file-path", str(log_path)]
    for _, cli_flag in GNB_LOG_CLI_FLAGS:
        args.extend([cli_flag, GNB_DEFAULT_LOG_LEVEL])
    return args


def read_gnb_logs(network: dict, lines: int = 200) -> dict:
    info = gnb_log_status(network)
    if not info["enabled"]:
        return {
            **info,
            "log": "",
            "source": "none",
            "message": "gnb 文件日志未启用",
        }

    log_path = resolve_gnb_log_path(network)
    file_log = tail_log(log_path, lines) if log_path.is_file() else ""
    stdout_log = tail_log(GNB_STDOUT_LOG_FILE, lines)

    if file_log.strip():
        return {
            **info,
            "path": str(log_path),
            "exists": True,
            "log": file_log,
            "source": "file",
        }

    if stdout_log.strip():
        return {
            **info,
            "path": str(log_path),
            "exists": log_path.is_file(),
            "log": stdout_log,
            "source": "stdout",
            "stdout_path": str(GNB_STDOUT_LOG_FILE),
            "message": (
                "gnb 文件日志尚未写入，以下为管理端捕获的 stdout"
                f"（{GNB_STDOUT_LOG_FILE}）。请重新「生成全部配置」并重启 gnb。"
            ),
        }

    return {
        **info,
        "path": str(log_path),
        "exists": log_path.is_file(),
        "log": "",
        "source": "none",
        "message": "暂无日志；请确认 gnb 已启动，并已重新生成配置后重启",
    }


def all_node_ids(network: dict) -> list[int]:
    ids = [network["index_forward"]["nodeid"]]
    ids.extend(c["nodeid"] for c in network.get("clients", []))
    return ids


def parse_extra_listens(raw, primary_listen: int) -> list[int]:
    """解析额外固定 listen 端口，最多 4 个（与主端口合计最多 5 个）。"""
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = [p.strip() for p in str(raw).split(",") if p.strip()]
    seen = {int(primary_listen)}
    result: list[int] = []
    for item in items:
        try:
            port = int(item)
        except (TypeError, ValueError):
            continue
        if port < 1 or port > 65535 or port in seen:
            continue
        seen.add(port)
        result.append(port)
        if len(result) >= 4:
            break
    return result


def node_opts_for(network: dict, nodeid: int, is_index: bool) -> dict:
    defaults = INDEX_NODE_DEFAULTS if is_index else CLIENT_NODE_DEFAULTS
    if is_index:
        src = network["index_forward"]
    else:
        src = next(c for c in network["clients"] if c["nodeid"] == nodeid)
    merged = {**defaults, **{k: src.get(k, defaults[k]) for k in defaults}}
    if is_index and not src.get("enable_forward", True):
        merged["set_fwdu0"] = "off"
        merged["direct_forwarding"] = "off"
    return merged


def append_optional_conf_line(
    lines: list[str],
    key: str,
    val: str,
    skip: set[str] | None = None,
) -> None:
    text = (val or "").strip()
    if not text:
        return
    if skip and text.lower() in skip:
        return
    lines.append(f"{key} {text}")


def append_client_platform_lines(lines: list[str], opts: dict, is_index: bool) -> None:
    """Windows 客户端写入 if-drv（Linux 不需要）。"""
    if is_index:
        return
    platform = str(opts.get("platform", "linux")).strip().lower()
    if platform != "windows":
        return
    if_drv = str(opts.get("if_drv", "wintun")).strip().lower()
    if if_drv not in ("wintun", "tap-windows"):
        if_drv = "wintun"
    lines.append(f"if-drv {if_drv}")


def append_gnb_advanced_lines(lines: list[str], opts: dict, is_index: bool) -> None:
    """写入 node.conf 支持的高级 gnb 选项（OpenGNB 1.6.x）。"""
    if str(opts.get("safe_index", "off")).strip().lower() == "on":
        lines.append("safe-index on")

    append_optional_conf_line(lines, "memory", opts.get("memory", ""), {"", "tiny"})
    if str(opts.get("zip", "")).strip().lower() == "force":
        lines.append("zip force")

    zip_level = str(opts.get("zip_level", "")).strip()
    if zip_level.isdigit() and int(zip_level) > 0:
        lines.append(f"zip-level {zip_level}")

    if str(opts.get("detect_interval", "")).strip():
        append_optional_conf_line(lines, "detect-interval", opts["detect_interval"])

    pf_worker = str(opts.get("pf_worker", "0")).strip()
    if pf_worker.isdigit() and int(pf_worker) > 0:
        lines.append(f"pf-worker {pf_worker}")

    append_optional_conf_line(lines, "pf-route-bits", opts.get("pf_route_bits", ""))


def build_extra_gnb_cli(opts: dict, is_index: bool) -> list[str]:
    """node.conf 未收录的选项，通过 gnb 命令行附加参数传递。"""
    args: list[str] = []
    cki = str(opts.get("crypto_key_update_interval", "")).strip().lower()
    if cki in ("hour", "minute"):
        args.extend(["--crypto-key-update-interval", cki])

    mft = str(opts.get("multi_forward_type", "")).strip().lower()
    if mft == "simple-load-balance":
        args.extend(["--multi-forward-type", mft])

    queue_fields = [
        ("node_worker_queue", "--node-worker-queue", True),
        ("index_worker_queue", "--index-worker-queue", False),
        ("index_service_worker_queue", "--index-service-worker-queue", False),
        ("packet_filter_worker_queue", "--packet-filter-worker-queue", True),
    ]
    for field, cli_flag, for_all_nodes in queue_fields:
        if not for_all_nodes and not is_index:
            continue
        val = str(opts.get(field, "")).strip()
        if val and val != GNB_DEFAULT_WORKER_QUEUE:
            args.extend([cli_flag, val])
    return args


def start_windows_script_content(extra_args: list[str]) -> str:
    args_part = ""
    if extra_args:
        args_part = " " + " ".join(extra_args)
    return (
        "@echo off\r\n"
        "rem OpenGNB 启动脚本（Windows；if-drv 见 node.conf）\r\n"
        "cd /d \"%~dp0..\"\r\n"
        "if not defined GNB_BIN set \"GNB_BIN=gnb.exe\"\r\n"
        "echo 配置目录: %CD%\r\n"
        "echo 请确保以管理员身份运行，且 bin 目录有 gnb_es.exe 与 wintun.dll\r\n"
        "\"%GNB_BIN%\" -c \"%CD%\" --console-log-level 3"
        f"{args_part}\r\n"
        "if errorlevel 1 pause\r\n"
    )


def start_linux_script_content(extra_args: list[str]) -> str:
    args_part = ""
    if extra_args:
        args_part = " " + " ".join(shlex.quote(a) for a in extra_args)
    return (
        "#!/bin/sh\n"
        "# OpenGNB 启动脚本（含 node.conf 无法表达的 CLI 参数）\n"
        'cd "$(dirname "$0")/.."\n'
        'exec "${GNB_BIN:-gnb}" -c "$(pwd)"'
        f"{args_part}\n"
    )


def build_es_argv_line(opts: dict, is_index: bool) -> str | None:
    es_argv = (opts.get("es_argv") or "").strip()
    if not is_index and opts.get("discover_in_lan"):
        if "-L" not in es_argv and "discover-in-lan" not in es_argv:
            es_argv = f"{es_argv} -L".strip() if es_argv else "-L"
    if not es_argv:
        return None
    if " " in es_argv and not (es_argv.startswith('"') and es_argv.endswith('"')):
        return f'es-argv "{es_argv}"'
    return f"es-argv {es_argv}"


def append_onoff(lines: list[str], opts: dict, key: str, conf_name: str) -> None:
    val = str(opts.get(key, "")).strip().lower()
    if val in ("on", "off"):
        lines.append(f"{conf_name} {val}")


def node_conf_content(
    nodeid: int,
    listen: int,
    is_index: bool,
    passcode: str,
    opts: dict,
) -> str:
    multi_socket, extra_listens = node_socket_opts_from(opts, listen)
    lines = [
        f"nodeid {nodeid}",
        f"listen {listen}",
    ]
    for port in extra_listens:
        lines.append(f"listen {port}")
    if passcode:
        lines.append(f"passcode {passcode}")
    lines.append(f"multi-socket {multi_socket}")

    if is_index:
        lines.append("set-tun off")
        append_onoff(lines, opts, "index_worker", "index-worker")
        append_onoff(lines, opts, "index_service_worker", "index-service-worker")
    append_onoff(lines, opts, "node_detect_worker", "node-detect-worker")

    append_onoff(lines, opts, "set_fwdu0", "set-fwdu0")
    append_onoff(lines, opts, "direct_forwarding", "direct-forwarding")

    uf = str(opts.get("unified_forwarding", "")).strip().lower()
    if uf in ("off", "auto", "super", "hyper"):
        lines.append(f"unified-forwarding {uf}")

    ip_stack = str(opts.get("ip_stack", "both")).strip().lower()
    if ip_stack == "ipv4":
        lines.append("ipv4-only")
    elif ip_stack == "ipv6":
        lines.append("ipv6-only")

    mtu = str(opts.get("mtu", "")).strip()
    if mtu.isdigit():
        lines.append(f"mtu {mtu}")

    es_line = build_es_argv_line(opts, is_index)
    if es_line:
        lines.append(es_line)

    append_client_platform_lines(lines, opts, is_index)
    append_gnb_advanced_lines(lines, opts, is_index)
    append_gnb_log_lines(lines, nodeid, opts, is_index)

    return "\n".join(lines) + "\n"


def append_gnb_log_lines(
    lines: list[str], nodeid: int, opts: dict, is_index: bool
) -> None:
    if not is_index or not opts.get("gnb_log_enabled"):
        return
    log_path = resolve_gnb_log_path(
        {"index_forward": {**opts, "nodeid": nodeid}}
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    append_gnb_log_level_lines(lines)
    lines.append(f"log-file-path {log_path}")


def node_socket_opts_from(opts: dict, listen: int) -> tuple[str, list[int]]:
    multi_socket = str(opts.get("multi_socket") or "off").strip().lower()
    if multi_socket not in ("on", "off"):
        multi_socket = "off"
    extra = parse_extra_listens(opts.get("extra_listens"), listen)
    return multi_socket, extra


def tun_ip_to_nodeid(network: dict, tun_ip: str) -> int:
    idx = network["index_forward"]
    if tun_ip == idx["tun_ip"]:
        return idx["nodeid"]
    for client in network.get("clients", []):
        if client["tun_ip"] == tun_ip:
            return client["nodeid"]
    raise ValueError(f"无法找到 TUN IP {tun_ip} 对应的 nodeid")


def route_conf_content(network: dict, for_nodeid: int) -> str:
    idx = network["index_forward"]
    lines = []
    seen = set()

    def add_route(nid: int, tun_ip: str, netmask: str) -> None:
        key = (nid, tun_ip, netmask)
        if key in seen:
            return
        seen.add(key)
        lines.append(f"{nid}|{tun_ip}|{netmask}")

    add_route(idx["nodeid"], idx["tun_ip"], idx["netmask"])
    for client in network.get("clients", []):
        add_route(client["nodeid"], client["tun_ip"], idx["netmask"])

    if for_nodeid != idx["nodeid"]:
        for client in network.get("clients", []):
            if client["nodeid"] == for_nodeid:
                for route in client.get("lan_routes", []):
                    via_nid = route.get("via_nodeid") or tun_ip_to_nodeid(
                        network, route["via_tun_ip"]
                    )
                    add_route(via_nid, route["network"], route["netmask"])
                break

    return "\n".join(lines) + "\n"


def index_address_attrib(network: dict) -> str:
    idx = network["index_forward"]
    return "if" if idx.get("enable_forward", True) else "i"


def address_conf_content(network: dict, for_nodeid: int) -> str:
    idx = network["index_forward"]
    attrib = index_address_attrib(network)
    return f"{attrib}|{idx['nodeid']}|{idx['public_ip']}|{idx['listen']}\n"


def if_up_script_content(network: dict, for_nodeid: int) -> str | None:
    for client in network.get("clients", []):
        if client["nodeid"] != for_nodeid:
            continue
        cmds = []
        for route in client.get("lan_routes", []):
            cidr = _netmask_to_prefix(route["netmask"])
            cmds.append(f"ip route add {route['network']}/{cidr} via {route['via_tun_ip']}")
        if cmds:
            return "#!/bin/sh\n" + "\n".join(cmds) + "\n"
    return None


def _netmask_to_prefix(netmask: str) -> int:
    parts = [int(x) for x in netmask.split(".")]
    bits = "".join(f"{p:08b}" for p in parts)
    return bits.count("1")


def run_crypto(crypto_bin: str, work_dir: Path, nodeid: int) -> tuple[Path, Path]:
    crypto_bin = resolve_binary(crypto_bin, "gnb_crypto", "gnb_crypto")
    priv = (work_dir / "security" / f"{nodeid}.private").resolve()
    pub = (work_dir / "security" / f"{nodeid}.public").resolve()
    priv.parent.mkdir(parents=True, exist_ok=True)
    cmd = [crypto_bin, "-c", "-p", str(priv), "-k", str(pub)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"返回码 {result.returncode}"
        raise RuntimeError(f"gnb_crypto 执行失败: {detail}")
    if not priv.exists() or not pub.exists():
        raise RuntimeError("gnb_crypto 执行后未生成密钥文件")
    return priv, pub


def node_key_paths(node_dir: Path, nodeid: int) -> tuple[Path, Path]:
    sec_dir = node_dir / "security"
    return sec_dir / f"{nodeid}.private", sec_dir / f"{nodeid}.public"


def node_keys_exist(node_dir: Path, nodeid: int) -> bool:
    priv, pub = node_key_paths(node_dir, nodeid)
    return priv.is_file() and pub.is_file()


def ensure_node_keys(
    crypto_bin: str,
    node_dir: Path,
    nodeid: int,
    regenerate: bool,
) -> tuple[Path, bool]:
    """返回 (公钥路径, 是否本次新生成)。"""
    if not regenerate and node_keys_exist(node_dir, nodeid):
        return node_key_paths(node_dir, nodeid)[1], False
    _, pub = run_crypto(crypto_bin, node_dir, nodeid)
    return pub, True


def cleanup_obsolete_node_dirs(node_ids: list[int]) -> None:
    if not CONF_DIR.exists():
        return
    valid = set(node_ids)
    for entry in CONF_DIR.iterdir():
        if entry.is_dir() and entry.name.isdigit() and int(entry.name) not in valid:
            shutil.rmtree(entry)


def distribute_ed25519_keys(node_ids: list[int], pub_keys: dict[int, Path]) -> None:
    for nodeid in node_ids:
        ed_dir = CONF_DIR / str(nodeid) / "ed25519"
        if ed_dir.exists():
            shutil.rmtree(ed_dir)
        ed_dir.mkdir(parents=True, exist_ok=True)
        for other_id, pub_path in pub_keys.items():
            if other_id == nodeid:
                continue
            shutil.copy2(pub_path, ed_dir / f"{other_id}.public")


def generate_passcode() -> str:
    return secrets.token_hex(4)


# ============================================================
# 核心功能
# ============================================================


def generate_all_configs(network: dict, regenerate_keys: bool = False) -> dict:
    ensure_dirs()
    idx = network["index_forward"]
    if not idx.get("public_ip"):
        raise ValueError("请填写公网 Index 节点的「公网 IP」（Index 节点配置区第二行）")
    if not idx.get("passcode"):
        idx["passcode"] = generate_passcode()
        network["index_forward"] = idx

    node_ids = all_node_ids(network)
    crypto_bin = network.get("gnb_crypto_bin", "gnb_crypto")

    cleanup_obsolete_node_dirs(node_ids)
    CONF_DIR.mkdir(parents=True, exist_ok=True)

    pub_keys: dict[int, Path] = {}
    keys_regenerated: list[int] = []
    keys_preserved: list[int] = []

    for nodeid in node_ids:
        node_dir = CONF_DIR / str(nodeid)
        node_dir.mkdir(parents=True, exist_ok=True)
        is_index = nodeid == idx["nodeid"]
        listen = idx["listen"] if is_index else next(
            c["listen"] for c in network["clients"] if c["nodeid"] == nodeid
        )
        opts = node_opts_for(network, nodeid, is_index)

        (node_dir / "node.conf").write_text(
            node_conf_content(
                nodeid,
                listen,
                is_index,
                idx["passcode"],
                opts,
            ),
            encoding="utf-8",
        )
        (node_dir / "address.conf").write_text(
            address_conf_content(network, nodeid),
            encoding="utf-8",
        )
        (node_dir / "route.conf").write_text(
            route_conf_content(network, nodeid),
            encoding="utf-8",
        )

        pub, created = ensure_node_keys(
            crypto_bin, node_dir, nodeid, regenerate_keys
        )
        pub_keys[nodeid] = pub
        if created:
            keys_regenerated.append(nodeid)
        else:
            keys_preserved.append(nodeid)

        scripts_dir = node_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        extra_cli = build_extra_gnb_cli(opts, is_index)
        client_platform = (
            str(opts.get("platform", "linux")).strip().lower()
            if not is_index
            else "linux"
        )
        if is_index or client_platform != "windows":
            start_path = scripts_dir / "start_linux.sh"
            start_path.write_text(start_linux_script_content(extra_cli), encoding="utf-8")
            os.chmod(start_path, 0o755)
        if not is_index and client_platform == "windows":
            win_start = scripts_dir / "start_windows.bat"
            win_start.write_text(start_windows_script_content(extra_cli), encoding="utf-8")

        script = if_up_script_content(network, nodeid)
        if script and client_platform != "windows":
            script_path = scripts_dir / "if_up_linux.sh"
            script_path.write_text(script, encoding="utf-8")
            os.chmod(script_path, 0o755)

    distribute_ed25519_keys(node_ids, pub_keys)

    save_network(network)
    return {
        "nodeids": node_ids,
        "passcode": idx["passcode"],
        "conf_dir": str(CONF_DIR),
        "keys_regenerated": keys_regenerated,
        "keys_preserved": keys_preserved,
        "regenerate_keys": regenerate_keys,
    }


def find_gnb_pids() -> list[int]:
    """查找机器上正在运行的 gnb 进程 PID。"""
    pids: set[int] = set()
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq gnb.exe", "/FO", "LIST"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in result.stdout.splitlines():
            if line.strip().startswith("PID:"):
                try:
                    pids.add(int(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
        return sorted(pids)

    if shutil.which("pgrep"):
        for pattern in ("-x", "gnb"), ("-f", r"[\/]gnb(\s|$)"):
            result = subprocess.run(
                ["pgrep", pattern[0], pattern[1]],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                for part in result.stdout.split():
                    if part.isdigit():
                        pids.add(int(part))
    return sorted(pids)


def kill_pid_force(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        os.kill(pid, signal.SIGKILL)


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def is_running() -> bool:
    return len(find_gnb_pids()) > 0


def validate_index_config(node_dir: Path) -> None:
    required = ["node.conf", "address.conf", "route.conf"]
    missing = [name for name in required if not (node_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"配置不完整，缺少: {', '.join(missing)}")
    sec_dir = node_dir / "security"
    if not sec_dir.exists() or not any(sec_dir.glob("*.private")):
        raise FileNotFoundError("缺少密钥文件，请先点击「生成全部配置」")


def start_index_forward(network: dict) -> dict:
    running = find_gnb_pids()
    if running:
        return {
            "ok": True,
            "message": f"机器上已有 gnb 在运行 (PID: {', '.join(map(str, running))})",
            "pid": running[0],
            "pids": running,
        }

    idx = network["index_forward"]
    if not idx.get("public_ip"):
        raise ValueError("请填写公网 Index 节点的「公网 IP」（Index 节点配置区第二行）")

    node_dir = CONF_DIR / str(idx["nodeid"])
    if not node_dir.exists():
        raise FileNotFoundError(
            f"配置目录不存在 ({node_dir})，请先点击「生成全部配置」"
        )
    validate_index_config(node_dir)

    gnb_bin = resolve_binary(network.get("gnb_bin", "gnb"), "gnb", "gnb")
    ensure_gnb_log_dir(network)
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = GNB_STDOUT_LOG_FILE

    opts = node_opts_for(network, idx["nodeid"], True)
    cmd = [
        gnb_bin,
        "-c",
        str(node_dir.resolve()),
        *build_extra_gnb_cli(opts, True),
        *build_gnb_log_cli(network),
    ]
    with log_file.open("a", encoding="utf-8") as log_f:
        log_f.write(
            f"\n[{datetime.datetime.now().isoformat()}] 启动: {' '.join(cmd)}\n"
        )
        popen_kw: dict = {
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
        }
        if os.name != "nt":
            popen_kw["start_new_session"] = True
        proc = subprocess.Popen(cmd, **popen_kw)

    time.sleep(0.5)
    if proc.poll() is not None:
        hint = tail_log(log_file)
        gnb_log = resolve_gnb_log_path(network)
        if network["index_forward"].get("gnb_log_enabled") and gnb_log.is_file():
            file_hint = tail_log(gnb_log, 30)
            if file_hint:
                hint = (hint + "\n" if hint else "") + f"[gnb 文件日志 {gnb_log}]\n{file_hint}"
        raise RuntimeError(
            f"gnb 启动后立即退出 (code={proc.returncode})。"
            + (f"\n最近日志:\n{hint}" if hint else " 请检查 gnb 路径与配置文件。")
        )

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "message": "Index+Forward 已启动", "pid": proc.pid}


def stop_index_forward() -> dict:
    """强制停止机器上所有 gnb 进程。"""
    targets = find_gnb_pids()
    killed: list[int] = []
    failed: list[int] = []

    for pid in targets:
        try:
            kill_pid_force(pid)
            killed.append(pid)
        except OSError:
            failed.append(pid)

    time.sleep(0.2)
    remaining = find_gnb_pids()

    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)

    if not targets and not remaining:
        return {"ok": True, "message": "机器上无 gnb 进程", "killed": [], "remaining": []}

    if remaining:
        return {
            "ok": False,
            "message": f"部分 gnb 未能停止，剩余 PID: {', '.join(map(str, remaining))}",
            "killed": killed,
            "failed": failed,
            "remaining": remaining,
        }

    return {
        "ok": True,
        "message": f"已强制停止 gnb 进程 (PID: {', '.join(map(str, killed))})",
        "killed": killed,
        "remaining": [],
    }


def client_platform(network: dict, nodeid: int) -> str:
    idx = network["index_forward"]
    if nodeid == idx["nodeid"]:
        return "linux"
    for c in network.get("clients", []):
        if c["nodeid"] == nodeid:
            return str(c.get("platform", "linux")).strip().lower() or "linux"
    return "linux"


def zip_readme_for_node(nodeid: int, network: dict) -> str:
    platform = client_platform(network, nodeid)
    lines = [
        f"OpenGNB 节点 {nodeid} 配置包",
        "",
        "目录结构（解压后）：",
        f"  gnb/conf/{nodeid}/node.conf",
        f"  gnb/conf/{nodeid}/address.conf",
        f"  gnb/conf/{nodeid}/route.conf",
        f"  gnb/conf/{nodeid}/security/",
        f"  gnb/conf/{nodeid}/ed25519/",
        f"  gnb/conf/{nodeid}/scripts/",
        "",
    ]
    if platform == "windows":
        lines.extend(
            [
                "【Windows 客户端】",
                "1. 将 gnb/conf/ 目录放到 gnb.exe 同级（推荐），或使用绝对路径 -c",
                "2. bin 目录需包含：gnb.exe、gnb_es.exe、wintun.dll（使用 wintun 时）",
                "3. 以管理员身份打开 PowerShell / CMD",
                f"4. 启动：gnb.exe -c gnb\\conf\\{nodeid}",
                f"   或：gnb\\conf\\{nodeid}\\scripts\\start_windows.bat",
                "5. node.conf 应含：if-drv wintun（管理端选 Windows 平台时自动生成）",
                "6. 若只打印版本信息后退出，加参数：--console-log-level 3 查看错误",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "【Linux / OpenWRT】",
                f"1. 将 gnb/conf/{nodeid}/ 放到 gnb 程序目录下",
                f"2. 启动：sudo gnb -c gnb/conf/{nodeid}",
                f"   或：sudo gnb/conf/{nodeid}/scripts/start_linux.sh",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def zip_node_config(nodeid: int, network: dict | None = None) -> bytes:
    network = network or load_network()
    node_dir = CONF_DIR / str(nodeid)
    if not node_dir.exists():
        raise FileNotFoundError(f"节点 {nodeid} 配置不存在，请先生成配置")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in node_dir.rglob("*"):
            if path.is_file():
                arcname = Path("gnb") / "conf" / str(nodeid) / path.relative_to(node_dir)
                zf.write(path, arcname)
        zf.writestr("README.txt", zip_readme_for_node(nodeid, network))
    buf.seek(0)
    return buf.read()


def zip_all_clients(network: dict) -> bytes:
    idx_id = network["index_forward"]["nodeid"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for client in network.get("clients", []):
            nodeid = client["nodeid"]
            node_dir = CONF_DIR / str(nodeid)
            if not node_dir.exists():
                continue
            for path in node_dir.rglob("*"):
                if path.is_file():
                    arcname = Path("gnb") / "conf" / str(nodeid) / path.relative_to(node_dir)
                    zf.write(path, arcname)
        readme_lines = [
            "OpenGNB 内网节点配置包（不含 Index+Forward 节点）",
            "",
            f"Index+Forward 节点 nodeid={idx_id} 请在管理端服务器上运行。",
            "",
        ]
        for client in network.get("clients", []):
            nid = client["nodeid"]
            if not (CONF_DIR / str(nid)).is_dir():
                continue
            plat = client.get("platform", "linux")
            readme_lines.append(
                f"节点 {nid}（{plat}）：见 gnb/conf/{nid}/scripts/ 下启动脚本"
            )
        readme_lines.append("")
        zf.writestr("README.txt", "\n".join(readme_lines) + "\n")
    buf.seek(0)
    return buf.read()


def client_config_ready(nodeid: int) -> bool:
    node_dir = CONF_DIR / str(nodeid)
    if not node_dir.is_dir():
        return False
    for name in ("node.conf", "address.conf", "route.conf"):
        if not (node_dir / name).is_file():
            return False
    sec_dir = node_dir / "security"
    return sec_dir.is_dir() and any(sec_dir.glob("*.private"))


def get_status(network: dict) -> dict:
    idx = network["index_forward"]
    node_dir = CONF_DIR / str(idx["nodeid"])
    clients_status = []
    for c in network.get("clients", []):
        nid = c["nodeid"]
        clients_status.append(
            {"nodeid": nid, "config_ready": client_config_ready(nid)}
        )
    gnb_pids = find_gnb_pids()
    return {
        "running": len(gnb_pids) > 0,
        "pid": gnb_pids[0] if gnb_pids else read_pid(),
        "pids": gnb_pids,
        "config_exists": node_dir.exists(),
        "conf_dir": str(CONF_DIR),
        "gnb_bin": network.get("gnb_bin", "gnb"),
        "gnb_target_version": TARGET_GNB_VERSION,
        "nodeid": idx["nodeid"],
        "gnb_log": gnb_log_status(network),
        "clients": clients_status,
    }


# ============================================================
# HTTP 服务
# ============================================================


class AdminHandler(BaseHTTPRequestHandler):
    server_version = "GNBAdmin/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            "[%s] %s - %s\n"
            % (self.log_date_time_string(), self.address_string(), fmt % args)
        )

    def _check_auth(self) -> bool:
        if not ADMIN_TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {ADMIN_TOKEN}":
            return True
        self._json_response(HTTPStatus.UNAUTHORIZED, {"error": "未授权"})
        return False

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": "JSON 格式错误"})
            return None

    def _json_response(self, status: HTTPStatus, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file_response(self, data: bytes, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, rel_path: str) -> None:
        if rel_path in ("", "/"):
            rel_path = "index.html"
        file_path = (STATIC_DIR / rel_path).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }
        ctype = content_types.get(file_path.suffix, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            if not self._check_auth():
                return
            qs = parse_qs(parsed.query)
            try:
                if path == "/api/network":
                    self._json_response(HTTPStatus.OK, load_network())
                elif path == "/api/status":
                    self._json_response(HTTPStatus.OK, get_status(load_network()))
                elif path == "/api/logs":
                    self._json_response(
                        HTTPStatus.OK,
                        {
                            "log": tail_log(GNB_STDOUT_LOG_FILE, 50),
                            "path": str(GNB_STDOUT_LOG_FILE),
                        },
                    )
                elif path == "/api/gnb-logs":
                    network = load_network()
                    lines = int(qs.get("lines", ["200"])[0])
                    lines = max(1, min(lines, 2000))
                    self._json_response(
                        HTTPStatus.OK,
                        read_gnb_logs(network, lines),
                    )
                elif path == "/api/download/all":
                    network = load_network()
                    data = zip_all_clients(network)
                    self._file_response(data, "gnb-clients.zip", "application/zip")
                elif path.startswith("/api/download/"):
                    nodeid = int(path.rsplit("/", 1)[-1])
                    data = zip_node_config(nodeid)
                    self._file_response(
                        data, f"gnb-node-{nodeid}.zip", "application/zip"
                    )
                else:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
            except FileNotFoundError as e:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": str(e)})
            except Exception as e:
                self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})
            return

        self._serve_static(path.lstrip("/"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._check_auth():
            return

        body = self._read_json()
        if body is None:
            return

        try:
            if parsed.path == "/api/network":
                current = merge_network(body)
                save_network(current)
                self._json_response(HTTPStatus.OK, {"ok": True, "network": current})

            elif parsed.path == "/api/generate":
                regenerate_keys = bool(body.get("regenerate_keys", False))
                network = merge_network(body)
                save_network(network)
                result = generate_all_configs(network, regenerate_keys=regenerate_keys)
                self._json_response(HTTPStatus.OK, {"ok": True, **result})

            elif parsed.path == "/api/start":
                network = merge_network(body)
                save_network(network)
                result = start_index_forward(network)
                self._json_response(HTTPStatus.OK, result)

            elif parsed.path == "/api/stop":
                result = stop_index_forward()
                status = HTTPStatus.OK if result.get("ok", True) else HTTPStatus.BAD_REQUEST
                self._json_response(status, result)

            else:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})
        except ValueError as e:
            self._json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
        except Exception as e:
            print(f"[错误] {parsed.path}: {e}", file=sys.stderr)
            traceback.print_exc()
            self._json_response(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})


# ============================================================
# 主流程
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenGNB 管理端")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    args = parser.parse_args()

    ensure_dirs()
    server = ThreadingHTTPServer((args.host, args.port), AdminHandler)

    print("=" * 60)
    print("    OpenGNB 管理端")
    print("=" * 60)
    print(f"  执行时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  访问地址: http://{args.host}:{args.port}/")
    print(f"  配置目录: {CONF_DIR}")
    if ADMIN_TOKEN:
        print("  鉴权: 已启用 GNB_ADMIN_TOKEN")
    else:
        print("  鉴权: 未启用（建议设置环境变量 GNB_ADMIN_TOKEN）")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[信息] 服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
