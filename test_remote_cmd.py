"""
向网关发送指令，反向调用本地 DSH 设备执行操作的测试工具
用法示例:
    1. 远程执行命令:
       uv run python test_remote_cmd.py "hostname"
       uv run python test_remote_cmd.py "dir"
       uv run python test_remote_cmd.py "ipconfig"
    2. 远程弹窗提醒:
       uv run python test_remote_cmd.py --notify "网关测试提醒" "成功通过网关调度本地电脑！"
    3. 远程打开网页:
       uv run python test_remote_cmd.py --open "https://github.com"
"""

import json
import sys
import urllib.request
import urllib.error

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 默认网关地址
GATEWAY_URL = "http://127.0.0.1:8790"
# 如果指定了环境变量或参数可以覆盖


def send_hub_call(action: str, body_args: dict, gateway_url: str = GATEWAY_URL):
    url = f"{gateway_url.rstrip('/')}/v1/hub/call"
    payload = json.dumps({"action": action, "body": body_args}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"\n📡 正在向网关 [{url}] 发送调度指令 [{action}]...")
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = resp.read().decode("utf-8")
            res = json.loads(data) if data else {}
            print(f"✅ 网关成功调度设备: \033[32m{res.get('device', '目标设备')}\033[0m (ID: {res.get('device_id', '')})")
            print("------------------------------------------------------------")
            result = res.get("result", {})
            if action == "shell":
                print(f"[退出码]: {result.get('exitCode', 0)}")
                if result.get("stdout"):
                    print("[标准输出]:\n" + result["stdout"].strip())
                if result.get("stderr"):
                    print("[错误输出]:\n" + result["stderr"].strip())
            else:
                print("[执行结果]:", json.dumps(result, ensure_ascii=False, indent=2))
            print("------------------------------------------------------------")
            return res
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_msg)
            print(f"❌ 调度失败 (HTTP {e.code}): {err_json.get('detail', {}).get('error', err_msg)}")
        except Exception:
            print(f"❌ 调度失败 (HTTP {e.code}): {err_msg}")
    except Exception as e:
        print(f"❌ 网络请求异常: {e}")


def main():
    args = sys.argv[1:]
    if not args:
        print("💡 用法说明:")
        print("   1. 执行本地命令: uv run python test_remote_cmd.py \"hostname\"")
        print("   2. 弹出系统通知: uv run python test_remote_cmd.py --notify \"提醒标题\" \"通知内容\"")
        print("   3. 远程打开网页: uv run python test_remote_cmd.py --open \"https://github.com\"")
        print("   4. 指定网关地址: uv run python test_remote_cmd.py --gateway http://47.93.151.39:8790 \"dir\"")
        return

    gateway = GATEWAY_URL
    if "--gateway" in args:
        idx = args.index("--gateway")
        gateway = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]

    if not args:
        return

    if args[0] == "--notify":
        title = args[1] if len(args) > 1 else "来自网关的通知"
        message = args[2] if len(args) > 2 else "测试通过网关调度本地电脑！"
        send_hub_call("notify", {"title": title, "body": message}, gateway)
    elif args[0] == "--open":
        target_url = args[1] if len(args) > 1 else "https://github.com"
        send_hub_call("open", {"url": target_url}, gateway)
    else:
        cmd = args[0]
        send_hub_call("shell", {"command": cmd}, gateway)


if __name__ == "__main__":
    main()
