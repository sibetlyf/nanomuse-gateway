#!/usr/bin/env python3
"""
nanoMuse Gateway 审计与状态查看工具
用于快速查看网关侧所有已注册账号、设备凭据（API Keys）、会话与在线拓扑。
运行方式:
  uv run python audit.py
"""
import os
import sys
import time
import sqlite3
import pathlib

# 确保 Windows 终端支持中文输出
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

DB_PATH = pathlib.Path(os.getenv("NANOMUSE_DB_PATH", pathlib.Path.home() / ".nanomuse" / "gateway.db"))

def format_time(ts: int | None) -> str:
    if not ts:
        return "从未"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))

def main():
    print("=" * 65)
    print("           nanoMuse 网关账号与设备审计工具")
    print("=" * 65)
    print(f"📁 数据库路径: {DB_PATH.resolve()}")

    if not DB_PATH.exists():
        print("⚠️  数据库文件尚未生成（网关可能尚未启动或未产生数据）。")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. 账号列表
    accounts = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
    print(f"\n👥 已注册账号列表 (共 {len(accounts)} 个):")
    print("-" * 65)
    print(f"{'账号标识(Identifier)':<20} {'账号内部ID':<24} {'创建时间':<20}")
    print("-" * 65)
    for acc in accounts:
        ident = acc["identifier"]
        acc_id = acc["id"][:16] + "..." if len(acc["id"]) > 16 else acc["id"]
        c_time = format_time(acc["created_at"])
        has_pwd = "🔒 有密码" if acc["password_hash"] else "🔓 免密"
        print(f"{ident:<20} {acc_id:<24} {c_time:<20} {has_pwd}")

    # 2. 绑定的设备与 Token
    keys = conn.execute(
        """
        SELECT k.key, k.device, k.created_at, k.last_used_at, k.revoked, a.identifier
        FROM api_keys k
        LEFT JOIN accounts a ON k.account_id = a.id
        ORDER BY k.last_used_at DESC
        """
    ).fetchall()

    print(f"\n📱 绑定的设备凭证 (共 {len(keys)} 个):")
    print("-" * 65)
    print(f"{'所属账号':<15} {'设备名称':<20} {'最后活跃时间':<20} {'状态':<6}")
    print("-" * 65)
    for k in keys:
        ident = str(k["identifier"] or "未知")
        dev = str(k["device"] or "未命名设备")[:18]
        last_used = format_time(k["last_used_at"])
        status = "已注销" if k["revoked"] else "有效"
        print(f"{ident:<15} {dev:<20} {last_used:<20} {status:<6}")

    print("=" * 65)

if __name__ == "__main__":
    main()
