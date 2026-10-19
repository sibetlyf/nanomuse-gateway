# nanoMuse Gateway (标准中继网关)

本项目是 **nanoMuse** 体系的独立中继网关服务 (The Hub)。

已**完整对齐原始 DSH 插件及移动端 App 的网关协议规范**，内置 SQLite 数据库持久化，**原生支持账号注册、密码登录、验证码注册、密码修改与智能体资料漫游同步**。

---

## 一、 支持的核心认证与业务接口

本网关实现了与官方中继完全一致的 HTTP RESTful API 与 WebSocket 路由：

| 接口地址 | 请求方式 | 作用说明 |
| :--- | :--- | :--- |
| **`/v1/auth/login`** | `POST` | **账号密码登录**：输入账号与密码即可。**若账号尚未注册，自动为其创建新账号并将此密码作为初始密码（一键注册）**。 |
| **`/v1/auth/code`** | `POST` | **请求验证码**：发送 6 位验证码（终端日志实时高亮打印，且**默认万能免输码 `123456` 永远有效**）。 |
| **`/v1/auth/verify`** | `POST` | **验证码登录/自动注册**：校验通过后签发设备 `api_key`，新账号自动初始化。 |
| **`/v1/auth/password`** | `POST` | **设置或修改密码**：已登录状态下设置或更改当前账号的登录密码。 |
| **`/v1/auth/sign-out-all`**| `POST` | **全部设备注销**：下线当前账号下的全部设备。 |
| **`/v1/me`** | `GET` | 获取当前账号详情、Token 额度池、是否已设置密码（`has_password`）等信息。 |
| **`/v1/me/sessions`** | `GET` | 查询当前账号下所有已登录的设备会话列表。 |
| **`/v1/me/profile`** | `GET/PUT` | 获取或同步保存智能体虚拟形象与名称，保存时通过 Hub 自动向同账号设备广播更新。 |
| **`/v1/devices`** | `GET` | 查询当前账号下的所有在线/离线设备拓扑列表。 |
| **`/v1/models`** | `GET` | 模型清单接口，供 DSH 和移动端在界面下拉框展示可用模型。 |
| **`/v1/hub`** | `WebSocket` | **双向全双工中继通道**：使用 `api_key` 自动划分账号隔离域，实现同账号设备间流式指令派发、屏幕抓取与审批流转。 |

---

## 二、 目录结构与数据持久化

```text
nanomuse-gateway/
├── gateway.py           # 核心网关服务源码 (FastAPI + SQLite + WebSockets)
├── start.sh             # Linux 下基于 uv 的服务管理启动脚本 (start/stop/restart/status/logs)
├── pyproject.toml       # 基于 uv 的标准应用配置
├── data/                # 数据持久化目录 (自动生成)
│   └── gateway.db       # SQLite 数据库文件 (持久保存账号、密码哈希、密钥及画像)
└── README.md            # 项目说明与配置指南
```

---

## 三、 Linux 云服务器启动命令

### 1. 赋予脚本执行权限
```bash
chmod +x start.sh
```

### 2. 常用管理命令
- **后台启动**：`./start.sh start` （基于 `uv` 自动准备环境并在后台运行）
- **查看状态**：`./start.sh status` （查看运行 PID、端口及健康探测）
- **实时日志**：`./start.sh logs` （查看设备接入、验证码打印及 RPC 调度日志）
- **重启服务**：`./start.sh restart`
- **停止服务**：`./start.sh stop`

> 默认监听端口为 `8790`。若需自定义端口，可执行：`PORT=9000 ./start.sh start`。  
> 请确保云服务器安全组已放行对应的 TCP 端口。

---

## 四、 客户端使用与登录方式

在电脑端 DSH 和手机端 App 将服务器地址指向您的公网服务器：  
`http://<您的云服务器公网IP>:8790`

您可以使用以下两种方式进行登录/注册：

### 方式 1：账号密码登录 / 首次使用自动注册（推荐）
- 在客户端的登录界面选择 **“Password（密码登录）”**；
- 填入您的用户名/邮箱（如 `admin@mycloud.com`）和密码（如 `12345678`）；
- 点击登录：
  - **新账号**：网关自动为您建立新账户并设置该密码；
  - **已有账号**：网关自动比对 PBKDF2 密码哈希，通过后即可登录！

### 方式 2：验证码登录 / 注册
- 在客户端输入手机号/邮箱后点击“获取验证码”；
- 验证码输入：**`123456`**（系统预置通用测试码），或者在服务器终端 `start.sh logs` 中查看实时生成的 6 位数字；
- 提交后自动完成登录，并在个人设置中可随时绑定或修改独立密码。

---

## 五、 多租户隔离说明

- 本网关支持**多账号并存**；
- 每个账号拥有独立的 `account_id`，只有登录**相同账号**的手机和电脑才能在 Hub 中互相发现和协同操作；
- 不同账号之间的设备、消息和会话完全隔离，具备严格的私密性与安全性。

---

## 六、 服务器端 admin 设备与 DSH 调试测试脚本

网关内置了用于诊断客户端连通性与 DSH 任务调度的自动化测试工具：`test_admin_devices.py`（配套快捷启动脚本 `test_admin.ps1`）。

### 1. 运行一键全功能测试 (推荐)
```bash
# Windows PowerShell
.\test_admin.ps1

# 或通用 uv 命令行
uv run --no-project --python 3.12 --with websockets python test_admin_devices.py
```
**测试执行流程**：
1. 自动使用 `admin` / `admin123` 向网关申请凭证并接入 WebSocket Hub；
2. 检索并列出当前 `admin` 账号下在线的所有设备；
3. 对每台设备依次发起：
   - **`ping`**：长连接心跳与往返延迟测算；
   - **`info`**：目标系统与运行环境探测；
   - **`task`**：**远程委托 DSH 任务**，验证对端电脑是否成功新建会话并唤起 Agent；
   - **`notify`**：向对端桌面推送系统弹窗测试通知。

### 2. 常用调试参数示例
```bash
# 仅测试 DSH 任务委托，并传入自定义提示词:
uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --action task --prompt "请列出当前项目中的核心文件"

# 针对远程云服务器上的网关进行诊断:
uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --gateway http://47.93.151.39:8790

# 仅做轻量 Ping 连通性探测:
uv run --no-project --python 3.12 --with websockets python test_admin_devices.py --action ping
```
