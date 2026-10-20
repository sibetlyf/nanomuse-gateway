"""
nanoMuse Gateway - admin 账号设备跨端连通性与 DSH 调试测试脚本
用于服务端一键检测 admin 账号下连接的所有设备，并对其发起远程指令 (ping, info, task, notify)
验证其能否正常响应并唤起 DSH 会话。

运行环境: UV
用法示例:
    1. 默认对所有设备执行完整连通性测试 (ping + info + task + notify):
       uv run --no-project --python 3.12 --with websockets python test_admin_devices.py
    2. 针对指定网关地址 (本地或远程服务器):
       uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --gateway http://47.93.151.39:8790
    3. 仅测试任务委托 (让远程电脑新建 DSH 对话):
       uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --action task --prompt "请列出当前项目中的核心文件"
    4. 仅测试 ping 延迟探测:
       uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --action ping
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request
import urllib.error
import uuid
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def get_api_key(gateway_url: str, identifier: str, password: str) -> str:
    """向网关鉴权接口获取 admin 账号的 API Key"""
    auth_url = f"{gateway_url.rstrip('/')}/v1/auth/credentials"
    payload = json.dumps({
        "identifier": identifier,
        "password": password,
        "device": "Gateway Admin TestRunner"
    }).encode("utf-8")

    req = urllib.request.Request(
        auth_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            api_key = data.get("api_key")
            if not api_key:
                raise RuntimeError(f"未能从网关获取到有效 api_key: {data}")
            return api_key
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        raise RuntimeError(f"认证失败 (HTTP {e.code}): {err}")
    except Exception as e:
        raise RuntimeError(f"连接网关鉴权接口失败 ({auth_url}): {e}")


async def test_device(ws, device: dict, test_action: str, prompt_text: str):
    """对单台设备执行指定的连通性与 DSH 任务测试"""
    dev_id = device.get("id", "未知ID")
    dev_name = device.get("name", "未知设备")
    actions = device.get("actions", [])

    print(f"\n============================================================")
    print(f"📱 目标设备: \033[1;36m{dev_name}\033[0m (ID: {dev_id})")
    print(f"   系统平台: {device.get('os', '未知')} | 类型: {device.get('kind', '未知')}")
    print(f"   声明能力: {', '.join(actions) if actions else '未声明'}")
    print(f"============================================================")

    async def call_action(act: str, args: dict = None, timeout_s: float = 15.0):
        call_id = f"test_{uuid.uuid4().hex[:8]}"
        frame = {
            "type": "call",
            "id": call_id,
            "to": dev_id,
            "action": act,
            "args": args or {}
        }
        start_t = time.perf_counter()
        await ws.send(json.dumps(frame))
        
        # 等待该 call_id 的回执
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"等待对端响应超时 ({timeout_s}s)")
            
            try:
                msg_raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                msg = json.loads(msg_raw)
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(f"等待对端响应超时 ({timeout_s}s)")

            mtype = msg.get("type")
            mid = msg.get("id")

            if mid == call_id:
                # 捕获中间执行流事件 (tool 调用、流式文字等)
                if mtype == "event":
                    ebody = msg.get("body", {})
                    stage = ebody.get("stage")
                    if stage == "accepted":
                        print(f"        ⏳ [就绪状态]: \033[36m{ebody.get('message', '已就绪')}\033[0m", flush=True)
                    elif stage == "tool":
                        print(f"        ⚡ [DSH 调用工具]: \033[33m{ebody.get('name')}\033[0m({ebody.get('args', '')})", flush=True)
                    elif stage == "tool_result":
                        status_str = "\033[32m✔ 成功\033[0m" if ebody.get("ok") else "\033[31m✖ 失败\033[0m"
                        print(f"        🔧 [工具执行回执]: {status_str} - {ebody.get('summary', '')[:120]}", flush=True)
                    elif stage == "stream_event":
                        print(f"        📡 [DSH 原始事件]: \033[35m{ebody.get('eventType')}\033[0m", flush=True)
                    elif stage == "text":
                        print(f"        ✍️  [DSH 正在生成文本...]", flush=True)
                    continue

                cost_ms = (time.perf_counter() - start_t) * 1000
                if mtype == "result":
                    return True, msg.get("body", {}), cost_ms
                elif mtype == "error":
                    return False, msg.get("message", "调用失败"), cost_ms

    # 1. 测试 Ping
    if test_action in ("all", "ping"):
        print(f"  [1/4] 正在探测长连接响应 (ping)...", end=" ", flush=True)
        try:
            ok, res, cost = await call_action("ping", {}, timeout_s=5.0)
            if ok:
                print(f"\033[32m✔ 正常\033[0m (耗时: {cost:.1f}ms) 回复: {res}")
            else:
                print(f"\033[31m✖ 失败\033[0m: {res}")
        except Exception as e:
            print(f"\033[31m✖ 异常: {e}\033[0m")

    # 2. 测试 Info
    if test_action in ("all", "info"):
        print(f"  [2/4] 正在获取设备详细环境 (info)...", end=" ", flush=True)
        try:
            ok, res, cost = await call_action("info", {}, timeout_s=5.0)
            if ok:
                print(f"\033[32m✔ 正常\033[0m (耗时: {cost:.1f}ms)")
                print(f"        主机详情: {json.dumps(res, ensure_ascii=False)}")
            else:
                print(f"\033[31m✖ 失败\033[0m: {res}")
        except Exception as e:
            print(f"\033[31m✖ 异常: {e}\033[0m")

    # 3. 测试 DSH 会话创建与 Agent 委托 (task)
    if test_action in ("all", "task"):
        print(f"  [3/4] 正在委托 DSH 会话任务 (action: task)...", flush=True)
        task_text = prompt_text or "这是一条来自网关后端的自检指令，请验证 DSH 会话调度与 Agent 是否已正常就绪！"
        print(f"        委托需求: \"{task_text}\"")
        try:
            ok, res, cost = await call_action("task", {"text": task_text}, timeout_s=120.0)
            if ok:
                session_id = res.get("sessionId") or res.get("thread")
                output_text = res.get("text", "")
                print(f"        \033[32m✔ DSH 任务执行完成！\033[0m (总耗时: {cost:.1f}ms)")
                if session_id:
                    print(f"        - 会话 ID: \033[1;33m{session_id}\033[0m")
                print("        ------------------------------------------------------------")
                print(f"        \033[1;32m💬 【DSH 思考与回答输出】:\033[0m")
                if output_text:
                    for line in output_text.strip().splitlines():
                        print(f"        {line}")
                else:
                    print("        (无文本输出)")
                print("        ------------------------------------------------------------")
            else:
                print(f"        \033[31m✖ 任务委托失败\033[0m: {res}")
        except Exception as e:
            print(f"        \033[31m✖ 任务委托异常: {e}\033[0m")

    # 4. 测试系统通知弹窗 (notify)
    if test_action in ("all", "notify"):
        print(f"  [4/4] 正在推送测试系统通知 (notify)...", end=" ", flush=True)
        try:
            ok, res, cost = await call_action("notify", {
                "title": "网关自动化诊断",
                "body": f"设备 [{dev_name}] 与网关及 DSH 的连接链路完全畅通！"
            }, timeout_s=8.0)
            if ok:
                print(f"\033[32m✔ 正常送达\033[0m (耗时: {cost:.1f}ms) 回执: {res}")
            else:
                print(f"\033[31m✖ 失败\033[0m: {res}")
        except Exception as e:
            print(f"\033[31m✖ 异常: {e}\033[0m")


async def run_diagnostics(args):
    gateway_url = args.gateway.rstrip('/')
    identifier = args.user
    password = args.password

    print("==========================================================")
    print(f"     nanoMuse Gateway 客户端连接与 DSH 调用诊断工具        ")
    print("==========================================================")
    print(f"🌐 网关地址: {gateway_url}")
    print(f"👤 测试账号: {identifier}")
    print(f"🎯 测试动作: {args.action}")

    # 1. 登录获取 API Key
    print(f"\n[第 1 步] 正在请求网关获取账号凭据 Token...")
    try:
        api_key = get_api_key(gateway_url, identifier, password)
        print(f"✔ 鉴权成功！获取到 API Key: {api_key[:8]}...{api_key[-6:]}")
    except Exception as e:
        print(f"❌ 登录认证失败: {e}")
        return

    # 2. 建立 WebSocket 长连接
    ws_url = gateway_url.replace("http://", "ws://").replace("https://", "wss://") + "/v1/hub"
    print(f"\n[第 2 步] 正在接入网关长连接通道: {ws_url} ...")

    my_device_id = f"test_cli_{uuid.uuid4().hex[:6]}"
    try:
        async with websockets.connect(ws_url) as ws:
            # 发送 hello 帧
            hello_frame = {
                "type": "hello",
                "key": api_key,
                "device": {
                    "id": my_device_id,
                    "name": "Admin Diagnostic CLI",
                    "kind": "web",
                    "os": sys.platform,
                    "version": "1.0.5",
                    "actions": ["ping"]
                }
            }
            await ws.send(json.dumps(hello_frame))

            # 接收 welcome 帧并读取在线设备列表
            welcome_raw = await asyncio.wait_for(ws.recv(), timeout=6.0)
            welcome = json.loads(welcome_raw)

            if welcome.get("type") == "error":
                print(f"❌ 网关连接握手被拒绝: {welcome.get('message')}")
                return

            all_devices = welcome.get("devices", [])
            # 过滤掉当前测试客户端自己
            target_devices = [d for d in all_devices if d.get("id") != my_device_id]

            print(f"✔ 成功接入 Hub 中继！当前账号下在线总设备数: {len(all_devices)} 台")
            print(f"----------------------------------------------------------")

            if not target_devices:
                print(f"\n⚠️  【提示】当前账号 [{identifier}] 下暂未检测到其他在线设备！")
                print(f"   可能原因：")
                print(f"   1. 本地 DSH 插件尚未启动或尚未点击连接；")
                print(f"   2. 插件填写的网关地址不是当前地址 ({gateway_url})；")
                print(f"   3. 插件配置的账号不是 [{identifier}]。")
                print(f"   请在 DSH 设置中确认并连接后，再次运行此脚本。")
                return

            print(f"📡 检测到 {len(target_devices)} 台在线远程设备，开始逐一发起 DSH 调用验证：")

            for dev in target_devices:
                await test_device(ws, dev, args.action, args.prompt)

            print("\n==========================================================")
            print("🎉 所有在线设备诊断测试完成！")
            print("==========================================================")

    except Exception as e:
        print(f"❌ WebSocket 长连接通信失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="nanoMuse Gateway - admin 账号设备调用与 DSH 测试工具")
    parser.add_argument("--gateway", default="http://127.0.0.1:8790", help="网关 HTTP 地址 (默认: http://127.0.0.1:8790)")
    parser.add_argument("--user", default="admin", help="测试登录账号 (默认: admin)")
    parser.add_argument("--password", default="admin123", help="测试连接密码 (默认: admin123)")
    parser.add_argument("--action", default="all", choices=["all", "ping", "info", "task", "notify"],
                        help="测试动作: all(全部), ping, info, task(DSH对话委托), notify(桌面弹窗)")
    parser.add_argument("--prompt", default="", help="当 action 为 task 时，自定义委托给 DSH 的任务提示词")

    args = parser.parse_args()
    asyncio.run(run_diagnostics(args))


if __name__ == "__main__":
    main()
