"""
nanoMuse 独立中继网关 (nanoMuse Relay Gateway)
完整实现与原始 DSH 插件及移动端完全一致的 HTTP API 与 Hub WebSocket 路由规范。
支持：
  1. 账号注册与密码登录 (POST /v1/auth/login)
  2. 验证码注册与登录 (POST /v1/auth/code & POST /v1/auth/verify)
  3. 修改/设置密码 (POST /v1/auth/password)
  4. 账号信息与会话管理 (GET /v1/me, GET /v1/me/sessions, DELETE /v1/me/sessions/{prefix})
  5. 智能体形象同步 (GET/PUT /v1/me/profile)
  6. 设备拓扑管理 (GET /v1/devices)
  7. 核心设备长连接中继 (WS /v1/hub)
数据使用内置 SQLite 持久化存储在 data/gateway.db。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
import uvicorn

# 日志输出配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gateway")

app = FastAPI(title="nanoMuse Gateway", version="1.0.5")

# 数据持久化目录与数据库路径
DATA_DIR = Path(os.getenv("GATEWAY_DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "gateway.db"

# 临时验证码缓存: identifier -> {"code": "123456", "expires_at": float}
verification_codes: Dict[str, Dict[str, Any]] = {}


# ==============================================================================
# 密码加密与 Token 辅助函数
# ==============================================================================


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """使用 PBKDF2-HMAC-SHA256 对密码加盐哈希"""
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    ).hex()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    new_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(new_hash, hashed)


def generate_api_key() -> str:
    """生成标准格式的 nanoMuse API Key"""
    return "nm_" + secrets.token_urlsafe(32)


# ==============================================================================
# 数据库存储层 (基于 SQLite)
# ==============================================================================


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._init_tables()

    def get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        with self.get_conn() as conn:
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                identifier TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                salt TEXT,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                device_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_used_at INTEGER NOT NULL,
                revoked INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS profiles (
                account_id TEXT PRIMARY KEY,
                rev INTEGER DEFAULT 1,
                data_json TEXT NOT NULL,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );
            """
            )

    def get_account_by_identifier(self, identifier: str) -> Optional[dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE identifier = ?", (identifier.strip(),)
            ).fetchone()
            return dict(row) if row else None

    def get_account_by_id(self, account_id: str) -> Optional[dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_account(
        self, identifier: str, password: str | None = None
    ) -> tuple[dict, bool]:
        """创建或返回已存在的账号；若提供了密码则设置密码哈希"""
        existing = self.get_account_by_identifier(identifier)
        if existing:
            return existing, False

        account_id = "acc-" + secrets.token_hex(8)
        now = int(time.time())
        pwd_hash, salt = hash_password(password) if password else (None, None)

        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO accounts (id, identifier, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, identifier.strip(), pwd_hash, salt, now),
            )
            # 初始化默认 Profile (小龙)
            default_profile = {
                "name": "nanoMuse",
                "avatar": "dragon",
                "emoji": "🐉",
                "color": "#0a66e4",
                "style": "friendly",
                "description": "Your personal assistant",
            }
            conn.execute(
                "INSERT INTO profiles (account_id, rev, data_json) VALUES (?, 1, ?)",
                (account_id, json.dumps(default_profile)),
            )

        account = self.get_account_by_id(account_id)
        assert account is not None
        return account, True

    def set_password(self, account_id: str, password: str | None):
        pwd_hash, salt = hash_password(password) if password else (None, None)
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE accounts SET password_hash = ?, salt = ? WHERE id = ?",
                (pwd_hash, salt, account_id),
            )

    def create_key(self, account_id: str, device_name: str) -> str:
        key = generate_api_key()
        now = int(time.time())
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO api_keys (key, account_id, device_name, created_at, last_used_at, revoked) VALUES (?, ?, ?, ?, ?, 0)",
                (key, account_id, (device_name or "Device")[:80], now, now),
            )
        return key

    def get_key_info(self, key: str) -> Optional[dict]:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key = ? AND revoked = 0",
                (key.strip(),),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE key = ?",
                (int(time.time()), key.strip()),
            )
            return dict(row)

    def revoke_key(self, key: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE key = ?", (key.strip(),)
            )

    def get_accounts_count(self) -> int:
        with self.get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()
            return int(row["c"]) if row else 0

    def revoke_all_keys(self, account_id: str):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE api_keys SET revoked = 1 WHERE account_id = ?", (account_id,)
            )

    def get_sessions(self, account_id: str) -> list[dict]:
        with self.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE account_id = ? AND revoked = 0 ORDER BY last_used_at DESC",
                (account_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_profile(self, account_id: str) -> dict:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT rev, data_json FROM profiles WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if not row:
                return {
                    "rev": 1,
                    "name": "nanoMuse",
                    "avatar": "dragon",
                    "emoji": "🐉",
                    "color": "#0a66e4",
                }
            data = json.loads(row["data_json"])
            data["rev"] = row["rev"]
            return data

    def update_profile(self, account_id: str, new_data: dict) -> int:
        with self.get_conn() as conn:
            current = self.get_profile(account_id)
            rev = current.get("rev", 1) + 1
            current.update(new_data)
            current.pop("rev", None)
            conn.execute(
                "INSERT OR REPLACE INTO profiles (account_id, rev, data_json) VALUES (?, ?, ?)",
                (account_id, rev, json.dumps(current)),
            )
            return rev


db = Database(DB_PATH)


def ensure_default_admin():
    """首次启动时自动创建默认 admin 账号（密码 admin123），存在则跳过"""
    existing = db.get_account_by_identifier("admin")
    if not existing:
        db.create_account("admin", password="admin123")
        log.info("✨ [初始化] 已创建默认账号 admin (密码: admin123)，请登录后尽快修改密码")
    else:
        # admin 账号已存在但若无密码则补充默认密码
        if not existing.get("password_hash"):
            db.set_password(existing["id"], "admin123")
            log.info("✨ [初始化] 已为现有 admin 账号设置默认密码 admin123")


# ==============================================================================
# Hub 中继连接与路由引擎 (按 account_id 严格隔离)
# ==============================================================================


class HubRouter:
    def __init__(self):
        # account_id -> { device_id -> {"ws": WebSocket, "info": dict, "connected_at": int} }
        self.online: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # (account_id, call_id) -> {"caller": str, "target": str, "started_at": int}
        self.pending: Dict[tuple[str, str], Dict[str, Any]] = {}

    def get_devices(self, account_id: str) -> list[dict]:
        now = int(time.time())
        account_pool = self.online.get(account_id, {})
        return [
            {
                "id": dev_id,
                "name": dev["info"].get("name", dev_id),
                "kind": dev["info"].get("kind", "computer"),
                "os": dev["info"].get("os", ""),
                "version": dev["info"].get("version", "1.0.5"),
                "actions": dev["info"].get("actions", []),
                "online": True,
                "last_seen": now,
                "controllable": dev["info"].get("kind") != "web",
            }
            for dev_id, dev in account_pool.items()
        ]

    async def broadcast_devices(self, account_id: str):
        devices = self.get_devices(account_id)
        frame = json.dumps({"type": "devices", "devices": devices})
        for dev in list(self.online.get(account_id, {}).values()):
            try:
                await dev["ws"].send_text(frame)
            except Exception:
                pass

    async def broadcast_profile(self, account_id: str, rev: int, sender_device: str):
        frame = json.dumps({"type": "profile", "rev": rev, "device": sender_device})
        for dev_id, dev in list(self.online.get(account_id, {}).items()):
            if dev_id != sender_device:
                try:
                    await dev["ws"].send_text(frame)
                except Exception:
                    pass

    async def drop_all_for_account(self, account_id: str):
        """当用户在某处点击全部登出时，断开同账号的所有连接"""
        for dev in list(self.online.get(account_id, {}).values()):
            try:
                await dev["ws"].close(code=4001, reason="sign_out_all")
            except Exception:
                pass
        self.online.pop(account_id, None)


hub = HubRouter()


# ==============================================================================
# 鉴权依赖注入
# ==============================================================================


async def get_current_account(authorization: Optional[str] = Header(None)) -> dict:
    """从 HTTP Authorization Header 中校验 Bearer Token 并取出当前账号"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "bad_key", "message": "Sign in required"}},
        )
    token = authorization[7:].strip()
    key_info = db.get_key_info(token)
    if not key_info:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "bad_key", "message": "Invalid or revoked key"}},
        )
    account = db.get_account_by_id(key_info["account_id"])
    if not account:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "account_gone", "message": "Account not found"}},
        )
    account["current_key"] = token
    return account


def build_account_sheet(account: dict) -> dict:
    """构建与客户端 relay.ts toAccount 完全一致的账号数据结构"""
    ident = account["identifier"]
    channel = "email" if "@" in ident else ("phone" if ident.isdigit() else "custom")
    return {
        "account": {
            "id": account["id"],
            "hint": ident,
            "channel": channel,
            "member": True,
            "has_password": bool(account.get("password_hash")),
            "created_at": account.get("created_at", int(time.time())),
        },
        "tokens": {
            "unlimited": True,
            "granted": 99999999,
            "used": 0,
            "remaining": 99999999,
        },
        "spend": {
            "total": 0.0,
            "left": None,
            "unlimited": True,
            "warn": False,
            "usd_cny": 7.2,
        },
        "contribute": {
            "on": False,
            "samples": 0,
            "default_on": False,
            "privacy_url": "",
        },
        "region": "cn",
    }


# ==============================================================================
# HTTP 路由实现：公开配置与健康探测
# ==============================================================================


@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "nanoMuse Gateway",
        "status": "running",
        "version": "1.0.5",
        "endpoints": {
            "healthz": "/healthz",
            "status": "/v1/gateway/status",
            "docs": "/docs",
            "hub": "/v1/hub",
        },
    }


@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "status": "ok",
        "version": "1.0.5",
        "accounts_online": len(hub.online),
    }


@app.get("/v1/config")
async def public_config():
    """客户端未登录时读取的全局配置参数"""
    return {
        "signup_open": True,
        "allowance_cny": 100.0,
        "invite_bonus_cny": 10.0,
        "version": "1.0.5",
    }


@app.get("/v1/nudges")
async def public_nudges():
    return {"policy": "off"}


@app.get("/v1/models")
async def models_list(_: dict = Depends(get_current_account)):
    """返回模型清单供 DSH 或移动端选择"""
    return {
        "data": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek Chat",
                "architecture": {"input_modalities": ["text"]},
                "nanomuse": {"kind": "chat", "recommended": True, "for": ["chat"]},
            },
            {
                "id": "qwen3.8-27b",
                "name": "Qwen 3.8 27B",
                "architecture": {"input_modalities": ["text", "image"]},
                "nanomuse": {
                    "kind": "chat",
                    "recommended": True,
                    "for": ["gui", "chat"],
                },
            },
        ]
    }


# ==============================================================================
# HTTP 路由实现：账号认证、注册与密码 (完整对齐原始 DSH 插件)
# ==============================================================================


@app.post("/v1/auth/code", status_code=204)
async def request_code(request: Request):
    """
    请求验证码:
    无论输入手机号、邮箱还是自定义账号，生成 6 位数字验证码并在终端打印。
    同时万能通用测试验证码 123456 始终有效。
    """
    body = await request.json()
    ident = str(body.get("identifier") or "").strip()
    if not ident:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "bad_identifier", "message": "Identifier required"}
            },
        )

    # 生成 6 位随机验证码
    code = f"{secrets.randbelow(900000) + 100000}"
    verification_codes[ident] = {"code": code, "expires_at": time.time() + 600}
    log.info(
        f"📨 [验证码通知] 账号 [{ident}] 的登录验证码为: {code} (通用免输码: 123456)"
    )
    return Response(status_code=204)


@app.post("/v1/auth/verify")
async def verify_code(request: Request):
    """
    验证码验证（首次输入自动注册新账号，否则登录现有账号）
    """
    body = await request.json()
    ident = str(body.get("identifier") or "").strip()
    code = str(body.get("code") or "").strip()
    device = str(body.get("device") or "Device")[:80]

    if not ident or not code:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "bad_request",
                    "message": "Identifier and code required",
                }
            },
        )

    # 校验验证码：支持万能码 123456 或后台真实生成的验证码
    valid = False
    if code == "123456":
        valid = True
    elif ident in verification_codes:
        item = verification_codes[ident]
        if item["expires_at"] >= time.time() and item["code"] == code:
            valid = True

    if not valid:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "code_wrong", "message": "验证码不正确或已过期"}},
        )

    # 创建或获取账号
    account, created = db.create_account(ident)
    api_key = db.create_key(account["id"], device)
    log.info(f"🔑 验证码登录成功: [{ident}], 新注册: {created}, 设备: {device}")

    sheet = build_account_sheet(account)
    return {"api_key": api_key, "created": created, **sheet}


@app.post("/v1/auth/login")
async def auth_login(request: Request):
    """
    密码登录：
    若账号存在且密码匹配，登录成功。
    若账号不存在，自动为其注册并把该密码设置为初始密码！(支持一键账号密码注册)
    """
    body = await request.json()
    ident = str(body.get("identifier") or "").strip()
    password = str(body.get("password") or "")
    device = str(body.get("device") or "Device")[:80]

    if not ident:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "bad_identifier", "message": "Identifier required"}
            },
        )
    if not password:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "password_required", "message": "Password required"}
            },
        )

    account = db.get_account_by_identifier(ident)
    created = False

    if not account:
        # 首次使用账号密码：自动为其注册
        account, created = db.create_account(ident, password=password)
        log.info(f"✨ 账号密码注册成功: [{ident}], 设备: {device}")
    else:
        # 账号已存在：验证密码
        if not account.get("password_hash") or not account.get("salt"):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": "password_not_set",
                        "message": "该账号尚未设置密码，请使用验证码登录",
                    }
                },
            )
        if not verify_password(password, account["password_hash"], account["salt"]):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": "password_wrong", "message": "账号或密码错误"}
                },
            )
        log.info(f"🔑 账号密码登录成功: [{ident}], 设备: {device}")

    api_key = db.create_key(account["id"], device)
    sheet = build_account_sheet(account)
    return {"api_key": api_key, "created": created, **sheet}


@app.get("/v1/gateway/status")
async def gateway_status():
    """网关系统运行状态探针，供 DSH Web Gateway 插件探测连接"""
    now = int(time.time())
    online_count = sum(len(d) for d in hub.online.values())
    return {
        "status": "online",
        "version": "1.0.5",
        "server_time": now,
        "accounts_count": db.get_accounts_count(),
        "online_devices_count": online_count,
        "features": ["password_auth", "code_auth", "hub_websocket", "credentials_sync"],
    }


@app.post("/v1/auth/credentials")
async def auth_credentials_sync(request: Request):
    """
    DSH Web 插件专用的网关凭据配置与同步接口：
    1. 若账号不存在：以给定的密码直接自动注册并返回有效凭据；
    2. 若账号已存在：验证原密码；
    3. 若指定了 new_password：在网关数据库中同步更新密码！
    """
    body = await request.json()
    ident = str(body.get("identifier") or "").strip()
    password = str(body.get("password") or "")
    new_password = str(body.get("new_password") or "").strip()
    device = str(body.get("device") or "DSH-Web-Plugin")[:80]

    if not ident:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "bad_identifier", "message": "Identifier required"}
            },
        )
    if not password:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "password_required", "message": "Password required"}
            },
        )

    account = db.get_account_by_identifier(ident)
    created = False
    password_updated = False

    if not account:
        # 首次配置：直接以账号密码自动注册创建
        target_pwd = new_password if new_password else password
        account, created = db.create_account(ident, password=target_pwd)
        log.info(f"✨ [Web插件] 账号密码自动注册成功: [{ident}], 设备: {device}")
    else:
        # 验证密码
        if account.get("password_hash") and account.get("salt"):
            if not verify_password(password, account["password_hash"], account["salt"]):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": "password_wrong",
                            "message": "账号或原密码错误",
                        }
                    },
                )
        # 同步修改为新密码
        if new_password:
            db.set_password(account["id"], new_password)
            password_updated = True
            log.info(f"🔒 [Web插件] 账号 [{ident}] 密码已同步修改更新")
        log.info(f"🔑 [Web插件] 账号密码鉴权成功: [{ident}], 设备: {device}")

    api_key = db.create_key(account["id"], device)
    sheet = build_account_sheet(account)
    return {
        "api_key": api_key,
        "created": created,
        "password_updated": password_updated,
        **sheet,
    }


@app.post("/v1/auth/password", status_code=204)
async def auth_set_password(
    request: Request, account: dict = Depends(get_current_account)
):
    """修改或首次设置密码"""
    body = await request.json()
    new_password = str(body.get("password") or "")
    current_password = body.get("current")

    # 如果已有密码，修改时需要校验旧密码
    if (
        account.get("password_hash")
        and account.get("salt")
        and current_password is not None
    ):
        if not verify_password(
            str(current_password), account["password_hash"], account["salt"]
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": "password_wrong", "message": "当前原密码不正确"}
                },
            )

    db.set_password(account["id"], new_password if new_password else None)
    log.info(f"🔒 账号 [{account['identifier']}] 密码已更新")
    return Response(status_code=204)


@app.post("/v1/auth/sign-out", status_code=204)
async def sign_out(account: dict = Depends(get_current_account)):
    """注销当前设备的 API Key"""
    db.revoke_key(account["current_key"])
    return Response(status_code=204)


@app.post("/v1/auth/sign-out-all", status_code=204)
async def sign_out_all(account: dict = Depends(get_current_account)):
    """下线该账号下的所有设备"""
    db.revoke_all_keys(account["id"])
    await hub.drop_all_for_account(account["id"])
    log.info(f"🚪 账号 [{account['identifier']}] 已下线全部设备")
    return Response(status_code=204)


# ==============================================================================
# HTTP 路由实现：用户信息、画像与会话
# ==============================================================================


@app.get("/v1/me")
async def get_me(account: dict = Depends(get_current_account)):
    return build_account_sheet(account)


@app.get("/v1/me/sessions")
async def get_sessions(account: dict = Depends(get_current_account)):
    sessions = db.get_sessions(account["id"])
    return {
        "sessions": [
            {
                "prefix": s["key"][:10],
                "device": s["device_name"],
                "created_at": s["created_at"],
                "last_used_at": s["last_used_at"],
                "current": s["key"] == account["current_key"],
            }
            for s in sessions
        ]
    }


@app.delete("/v1/me/sessions/{prefix}", status_code=204)
async def revoke_session_by_prefix(
    prefix: str, account: dict = Depends(get_current_account)
):
    sessions = db.get_sessions(account["id"])
    for s in sessions:
        if s["key"].startswith(prefix):
            db.revoke_key(s["key"])
            break
    return Response(status_code=204)


@app.get("/v1/me/profile")
async def get_profile(account: dict = Depends(get_current_account)):
    return db.get_profile(account["id"])


@app.put("/v1/me/profile")
async def update_profile(
    request: Request, account: dict = Depends(get_current_account)
):
    body = await request.json()
    sender_device = str(body.pop("device", "") or "")
    rev = db.update_profile(account["id"], body)
    await hub.broadcast_profile(account["id"], rev, sender_device)
    return {"rev": rev}


@app.get("/v1/devices")
async def get_devices(account: dict = Depends(get_current_account)):
    return hub.get_devices(account["id"])


@app.get("/v1/me/invite")
async def get_invite(account: dict = Depends(get_current_account)):
    return {
        "code": account["id"][:8],
        "url": "",
        "invites": 0,
        "bonus_cny": 0.0,
        "earned_cny": 0.0,
    }


@app.get("/v1/estimate")
async def estimate():
    return {"cny": 0.0, "left_cny": 99999.0, "unlimited": True, "affordable": True}


# ==============================================================================
# WebSocket 路由实现：Hub 跨设备全双工消息中继
# ==============================================================================


@app.websocket("/v1/hub")
async def hub_endpoint(ws: WebSocket):
    await ws.accept()
    account_id = None
    device_id = None
    dev_name = "unknown"

    try:
        # 1. 第一帧协商：等待 hello 帧
        try:
            raw_hello = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
        except asyncio.TimeoutError:
            await ws.close(code=4000, reason="hello expected timeout")
            return

        try:
            hello_data = json.loads(raw_hello)
        except ValueError:
            await ws.close(code=4000, reason="bad json in hello")
            return

        if hello_data.get("type") != "hello":
            await ws.close(code=4000, reason="hello expected")
            return

        # 认证提取 Token
        token = None
        auth_header = ws.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif hello_data.get("key"):
            token = str(hello_data.get("key")).strip()

        # 根据 Token 查出 account_id（如果未带 Token，分配至公共测试租户 default）
        if token:
            key_info = db.get_key_info(token)
            if not key_info:
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "error",
                            "code": "bad_key",
                            "message": "Invalid or revoked key",
                        }
                    )
                )
                await ws.close(code=4001, reason="bad_key")
                return
            account_id = key_info["account_id"]
        else:
            account_id = "default"

        dev_info = hello_data.get("device", {})
        device_id = str(dev_info.get("id") or f"client-{int(time.time()*1000)}")
        dev_name = str(dev_info.get("name") or device_id)
        dev_kind = str(dev_info.get("kind") or "computer")

        # 将设备登记入对应 account_id 的设备池
        account_pool = hub.online.setdefault(account_id, {})
        if device_id in account_pool:
            old_ws = account_pool[device_id]["ws"]
            try:
                await old_ws.close(code=4003, reason="replaced by a newer connection")
            except Exception:
                pass

        account_pool[device_id] = {
            "ws": ws,
            "info": dev_info,
            "connected_at": int(time.time()),
        }
        log.info(
            f"🟢 [设备接入] 账号: {account_id} | 设备: {dev_name} ({device_id}, 类型: {dev_kind})"
        )

        # 2. 回复 welcome 帧并向同账号其它设备广播拓扑
        await ws.send_text(
            json.dumps(
                {
                    "type": "welcome",
                    "device_id": device_id,
                    "devices": hub.get_devices(account_id),
                    "server": {
                        "version": "1.0.5",
                        "frame_limit": 16 * 1024 * 1024,
                        "time": int(time.time()),
                    },
                }
            )
        )
        await hub.broadcast_devices(account_id)

        # 3. 消息流转监听主循环
        while True:
            text = await ws.receive_text()
            try:
                frame = json.loads(text)
            except ValueError:
                continue

            ftype = frame.get("type")

            # 心跳
            if ftype == "ping":
                await ws.send_text(
                    json.dumps({"type": "pong", "time": int(time.time())})
                )

            # 请求刷新设备表
            elif ftype == "devices":
                await ws.send_text(
                    json.dumps(
                        {"type": "devices", "devices": hub.get_devices(account_id)}
                    )
                )

            # 协议级心跳响应 (ping -> pong)，维持长连接活跃防止对端被动超时关闭
            if ftype == "ping":
                await ws.send_text(
                    json.dumps({"type": "pong", "time": int(time.time() * 1000)})
                )
                continue
            elif ftype == "pong":
                continue

            # 跨设备指令下发 (call)
            elif ftype == "call":
                call_id = str(frame.get("id") or "")
                target_id = str(frame.get("to") or "")
                action = str(frame.get("action") or "")

                if not call_id or not target_id:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "id": call_id,
                                "code": "bad_frame",
                                "message": "Missing call id or target",
                            }
                        )
                    )
                    continue

                target = hub.online.get(account_id, {}).get(target_id)
                if not target:
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": "error",
                                "id": call_id,
                                "code": "device_offline",
                                "message": f"目标设备 [{target_id}] 当前离线",
                            }
                        )
                    )
                    continue

                # 挂起映射
                hub.pending[(account_id, call_id)] = {
                    "caller": device_id,
                    "target": target_id,
                    "started_at": int(time.time()),
                }

                log.info(
                    f"⚡ [跨端调度] 账号: {account_id} | 发起设备: {dev_name} ({device_id}) ➔ 目标设备: [{target_id}] | 动作: {action}"
                )

                # 转发给目标设备
                caller_brief = {
                    "id": device_id,
                    "name": dev_name,
                    "kind": dev_info.get("kind", "computer"),
                }
                forward_call = {
                    "type": "call",
                    "id": call_id,
                    "from": caller_brief,
                    "action": action,
                    "args": frame.get("args") or {},
                }
                await target["ws"].send_text(json.dumps(forward_call))

            # 执行流事件 (event) 与最终应答 (result)
            elif ftype in ("result", "event"):
                call_id = str(frame.get("id") or "")
                p = hub.pending.get((account_id, call_id))
                if not p:
                    continue

                caller_id = p["caller"]
                if ftype == "result":
                    hub.pending.pop((account_id, call_id), None)
                    log.info(
                        f"✅ [操作完成] 账号: {account_id} | 执行设备: {dev_name} ({device_id}) ➔ 响应调度 ID: {call_id}"
                    )

                caller = hub.online.get(account_id, {}).get(caller_id)
                if caller:
                    frame["from"] = {
                        "id": device_id,
                        "name": dev_name,
                        "kind": dev_info.get("kind", "computer"),
                    }
                    await caller["ws"].send_text(json.dumps(frame))

            # 设备更名
            elif ftype == "rename":
                new_name = str(frame.get("name") or "").strip()[:60]
                if new_name:
                    dev_info["name"] = new_name
                    dev_name = new_name
                    await hub.broadcast_devices(account_id)

            # 会话与工作状态漫游同步
            elif ftype in ("sync", "working"):
                for oid, oconn in list(hub.online.get(account_id, {}).items()):
                    if oid != device_id:
                        try:
                            await oconn["ws"].send_text(text)
                        except Exception:
                            pass

    except (WebSocketDisconnect, Exception) as exc:
        log.info(
            f"🔴 [设备断开] 账号: {account_id} | 设备: {dev_name} ({device_id}): {exc}"
        )
    finally:
        if account_id and device_id and device_id in hub.online.get(account_id, {}):
            del hub.online[account_id][device_id]
            # 清理未决任务并向发起端报错
            for (acc, cid), p in list(hub.pending.items()):
                if acc == account_id and p["target"] == device_id:
                    hub.pending.pop((acc, cid), None)
                    caller = hub.online.get(account_id, {}).get(p["caller"])
                    if caller:
                        try:
                            await caller["ws"].send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "id": cid,
                                        "code": "device_offline",
                                        "message": f"{dev_name} 在应答前意外离线",
                                    }
                                )
                            )
                        except Exception:
                            pass
            await hub.broadcast_devices(account_id)


# ==============================================================================
# 服务主入口
# ==============================================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8790"))
    host = os.getenv("HOST", "0.0.0.0")
    # 启动时确保 admin 默认账号已存在
    ensure_default_admin()
    log.info(f"🚀 nanoMuse 网关已就绪 (数据库路径: {DB_PATH.resolve()})")
    log.info(f"🌐 服务监听地址: http://{host}:{port}")
    log.info("💡 默认账号: admin / 密码: admin123 (请登录后及时修改)")
    uvicorn.run(app, host=host, port=port)
