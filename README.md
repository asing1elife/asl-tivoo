# Tivoo Codex 周额度显示

在 macOS 上读取当前 Codex 登录账户的周剩余额度，通过蓝牙显示到经典 Divoom Tivoo 的 16×16 像素屏。

![Tivoo 额度显示与流动彩虹背景预览](docs/images/demo.gif)

演示图片固定存放于 `docs/images/demo.gif`，百分比和重置日期为录制时的画面，不会随程序运行更新。

如需更新演示图片，可手动将 `output/preview.gif` 复制到该位置。

已在当前设备完成 RFCOMM 状态查询、测试图片发送和真实额度显示。用户已确认百分比显示正常。

## 运行

需要 Python 3.10+、Xcode Command Line Tools、已登录 ChatGPT 账户的 Codex CLI，以及已配对的 Tivoo。

```sh
./setup.sh
export TIVOO_MAC='AA:BB:CC:DD:EE:FF'
.venv/bin/python -m tivoo status
.venv/bin/python -m tivoo test --remaining 88
.venv/bin/python -m tivoo quota
.venv/bin/python -m tivoo once
.venv/bin/python -m tivoo watch
```

请将示例蓝牙地址 `AA:BB:CC:DD:EE:FF` 替换为自己的设备地址。

`test` 显示演示数值；`once` 和 `watch` 使用真实额度。设备地址可用全局参数 `--mac` 指定。

只生成本地预览：

```sh
.venv/bin/python -m tivoo once --preview-only
```

## 后台运行

### 交互管理

运行根目录的交互管理脚本：

```sh
./status.sh
```

脚本显示服务状态、PID、运行时长、登录自启配置及最近日志。

| 输入 | 操作 |
| --- | --- |
| `1` | 启动服务 |
| `2` | 重启服务 |
| `3` | 停止服务 |
| 回车 | 刷新状态 |
| `q` | 退出菜单 |

启动和重启复用已保存的设备配置；首次安装时才需要输入蓝牙地址。

停止仅影响本次登录，保留登录自启配置，音响也会保留最后一次画面。

### 命令行管理

也可以直接使用命令：

```sh
.venv/bin/python service.py start --mac AA:BB:CC:DD:EE:FF
.venv/bin/python service.py status
.venv/bin/python service.py stop
.venv/bin/python service.py uninstall
```

- `start`：安装当前用户的 LaunchAgent 并启动，登录后自动运行，无需 sudo。
- `stop`：停止本次运行。
- `uninstall`：停止服务，同时移除登录启动配置。

配置与日志位置：

- 配置：`~/Library/LaunchAgents/local.tivoo.codex-quota.plist`
- 日志：`output/service.log`

移动项目或更换 Codex 安装路径后，需要 `uninstall` 并重新 `start`。

### 刷新与动画

- 每 300 秒查询一次额度，画面变化时推送。
- 每 30 分钟强制刷新一次，以修复设备重启或手动切换画面。
- Mac 睡眠期间暂停，醒来后恢复轮询；设备离线会在下一轮重试。

每次推送上传完整的 16 帧动画，音响本地循环播放，电脑无需持续逐帧发送。

停止服务后，已上传的彩虹动画仍可继续播放，因此彩虹背景不是后台服务存活指示灯。

### 蓝牙重试与诊断

- **初始化**：每次调用均先运行事件循环，已连接的设备也等待 1 秒；控制器未开启时最多再等待 4 秒。
- **连接重试**：打开 RFCOMM 通道失败后，在同一进程内等待 1 秒、2 秒后重试，最多尝试 3 次；仍失败则交给下一轮轮询。
- **重试范围**：仅在开始发送前重试，不重复执行已发送的命令，也不自动开关 Mac 蓝牙或断开音响的音频连接。
- **诊断日志**：记录初始化、控制器状态、设备连接状态、尝试次数、错误码和耗时；成功重试的诊断也会保留。
- **总超时**：蓝牙子进程限时为 60 秒加每个数据块 0.2 秒，超时时保留已有诊断。

## 屏幕含义

- 顶部数字：周剩余百分比，向下取整；中间为 16 格进度条。
- 空白像素：35% HSV 明度的流动彩虹背景，每帧 100 毫秒，1.6 秒循环一次；百分比、进度条、日期和异常提示保持原色及位置。
- 进度条下方：3×3 小号像素字体显示北京时间的重置日期，格式为 `MMDD`（例如 `1014` 表示 10 月 14 日）；月份深蓝、日期浅蓝。
- 重置日期不可用时显示 `----`；旧数据已跨过重置时间且读取失败时，也不再显示旧日期。
- 剩余至少 30% 为绿色，低于 30% 为黄色，低于 10% 为红色。
- 右上角橙色感叹号：额度刷新失败，保留的是旧数值。
- 没有有效数据或旧数据已跨过重置时间：显示问号，不伪造 0% 或 100%。

### 本地预览文件

| 文件 | 内容 |
| --- | --- |
| `output/screen.png` | 16×16 首帧 |
| `output/preview.png` | 首帧放大预览 |
| `output/preview.gif` | 动画预览 |

这些文件表示待发送画面；成功以程序日志的设备确认与实机画面为准。

`output/` 为运行时输出目录，已由 Git 忽略，预览刷新不会产生待提交变更。

## 实现

### 蓝牙通信

Python 负责额度读取、像素绘图和轮询；Objective-C 桥接调用 macOS IOBluetooth RFCOMM 通道 1。

这台设备可直接通过 `Tivoo-audio` 控制像素屏，无需单独配对 `Tivoo-light`。

- 只有收到校验和有效且命令匹配的成功回复，才报告设备确认。
- 动画通过 `0x49` 命令在同一蓝牙连接内分块上传，每块最多 200 字节。
- 上传时检查蓝牙写入结果，以及整段动画接收完成的一次成功回复。

### 额度读取

额度通过 `codex app-server` 的 `account/rateLimits/read` 读取，复用本机 Codex 登录。

不直接读取、复制或保存访问令牌，不执行模型推理，不消耗额度重置机会。

额度选择规则：

- 优先选择 `rateLimitsByLimitId.codex`。
- 按 `windowDurationMins == 10080` 识别周窗口，不假定 primary/secondary 的位置。
- 不把其他模型额度或短周期额度当作周额度。
- 可通过全局 `--limit-id` 切换指定额度桶；不存在的额度会报错。

环境变量：

- `CODEX_BIN`：指定 Codex 可执行文件。
- `CODEX_HOME`：如已设置，程序会继承该配置。

### 参考资料

- [官方额度接口](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt)
- [蓝牙协议参考](https://github.com/solar2ain/tivoo-control)
- [第三方来源](THIRD_PARTY.md)

## 测试

```sh
.venv/bin/python -m unittest discover -s tests -v
```

覆盖周窗口选择、多额度桶优先级、数据缺失、像素编码解码、进度条边界、回复校验和、分片和未确认发送。
