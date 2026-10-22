# AirPods Control for Linux

Linux 上的 AirPods 控制中心：**实时电量、聆听模式、通透调节、系统托盘、配置预设**。

版本 0.2 新增：

- **总览**：左右耳和充电盒电量、充电状态、当前模式、型号、固件。未报告的数据明确显示未知。
- **模式切换**：降噪、通透、自适应、关闭，收到耳机确认后才报告成功。
- **系统托盘**：查看电量、切换模式、应用预设、重新打开窗口。系统支持托盘时，关闭窗口会收起到托盘。
- **配置预设**：保存当前已应用的声音参数，可选包含聆听模式；一键应用，原值自动备份。
- **共享后台服务**：GUI、托盘、CLI 共用一个持续 AACP 连接；同一用户的通透操作串行执行。
- **JSON 调试**：状态快照、变化事件、模式、预设、后台服务都能通过命令行操作。

提供中文 Qt 桌面界面和零第三方依赖的命令行。基于 LibrePods 已公开的协议重新实现，
通透调节只操作参数 `0x0018`，不操作助听器参数 `0x002A`、听力图或固件。
默认优先通过 BlueZ 的 D-Bus GATT 接口读写，未发现服务时才使用直接 ATT 连接。
命令行依赖系统的 `busctl`（systemd）和 `bluetoothctl`（BlueZ）。

**状态：已在一副 AirPods Pro 3 上完成实机读取、备份、写入和读回验证。**
Ubuntu 22.04 配置 Apple DeviceID、重启蓝牙并重新连接耳机后，原来的 `ATT 0x0e`
错误消失。已成功启用自定义通透，并把左右耳增音从 0 改为 −0.2；其余参数保持原值。
关闭参数连接后，新连接再次读取也确认了修改。听感效果、蓝牙重连后的保持情况和其他固件
仍需验证。具体记录见 [实机验证](docs/hardware-validation.md)。

![桌面界面（模拟设备）](docs/demo.png)

## 安装与运行

需要 Linux、Python 3.10+、BlueZ 和已配对并连接的 AirPods。

```sh
python3 -m venv .venv
.venv/bin/pip install '.[gui]'
.venv/bin/airpods-transparency devices
.venv/bin/airpods-transparency gui
```

也可以使用新命令 `.venv/bin/airpods-control gui`；原来的 `airpods-transparency` 命令保持兼容。

## 电量、模式和配置预设

GUI 包含「总览」「声音调节」「配置预设」三个页面，指定 `--mac` 时自动读取当前设置。
总览每三秒刷新后台状态；后台持续接收耳机通知。充电盒不报告电量时显示“未报告”，
不会把未知当作 0%。耳机断开后，旧电量不会继续当作实时电量显示。

```sh
airpods-control status --mac AA:BB:CC:DD:EE:FF --json
airpods-control watch --mac AA:BB:CC:DD:EE:FF --json
airpods-control mode anc --mac AA:BB:CC:DD:EE:FF --json
airpods-control mode transparency --mac AA:BB:CC:DD:EE:FF --json
airpods-control preset save "Office" --mac AA:BB:CC:DD:EE:FF --json
airpods-control preset list --json
airpods-control preset apply "Office" --mac AA:BB:CC:DD:EE:FF --json
airpods-control preset delete "Office" --json
```

以上命令假定虚拟环境已激活；也可使用 `.venv/bin/airpods-control` 完整前缀。
`watch` 输出 JSON Lines，在状态变化时输出，Ctrl+C 结束。模式可选
`anc`、`transparency`、`adaptive`、`off`。选择关闭时，会先启用耳机的“关闭聆听模式”选项。
某型号或固件不接受指定模式时，工具会报未确认，而不会假报成功。

预设保存的是**耳机当前已应用的**参数，不是尚未应用的 GUI 编辑值。
默认记录当前已知的聆听模式；加 `--without-mode` 可只保存声音设置。
预设存放于 `${XDG_CONFIG_HOME:-~/.config}/airpods-transparency/presets.json`，
可移植到其他设备，不包含地址、序列号或听力图。相同名称再次保存会覆盖该预设。
应用时保留目标设备原有 EQ；原值仍在写入前备份。模式切换若失败，会明确报告声音参数已应用、
模式未确认的部分成功状态，不自动反复修改设备。

托盘需要桌面环境支持系统托盘/StatusNotifier。支持时关闭窗口会收起到托盘，
通过托盘菜单退出界面；`gui --tray` 可直接从托盘启动。
不支持托盘的桌面会正常显示窗口，避免应用消失后无法找回。

## 后台服务与诊断

首次使用 GUI 或设备 CLI 时会自动启动用户级服务，不需要 sudo。
服务只为 BlueZ **已经连接**的耳机打开控制通道；断开后按退避策略重试，
不会主动连接耳机或抢占手机的音频。退出 GUI 后服务仍可供 CLI 使用。

```sh
airpods-control service start --json
airpods-control service status --json
airpods-control doctor --mac AA:BB:CC:DD:EE:FF --json
airpods-control service stop --json
```

`service status` 包含版本、PID 和本地日志路径；该命令不会主动启动服务。
日志默认位于 `${XDG_STATE_HOME:-~/.local/state}/airpods-transparency/service.log`，
按 1 MiB 轮转，保留两份历史。日志不保存原始蓝牙报文或设备序列号。
升级后先 `service stop`，下次使用将启动新版服务。

服务使用用户私有目录下的 Unix socket（0700 目录、0600 socket），双向检查本机用户身份；
没有 HTTP 监听端口。GUI 与 CLI 都通过 JSON 请求调用同一控制器，写请求中断后不会自动重发。

当前版本没有开机自动启动、摘耳自动暂停、全局快捷键、系统 EQ 或跨苹果设备自动切换，
这些属于后续路线图。后台已读取的佩戴状态只用于诊断，不会自动改变播放行为。

Ubuntu 如果缺少 venv：先安装 `python3-venv`。仅使用命令行可将 `'.[gui]'` 改为 `.`。
无需安装即可运行命令行：`python3 -m airpods_transparency --help`。
精简系统上 Qt 界面可能还需要系统运行库：`sudo apt install libegl1 libxcb-cursor0`。

界面：输入或查找耳机地址 → 读取设置 → 调整 → 备份并应用。
每项数值提供拖动滑条和精确输入框，双向同步；拖动只编辑界面，点击应用后才写入耳机。
“同步左右耳”默认开启：调整任一侧的增音、音调、环境降噪或对话增强，会同步修改另一侧。
取消勾选后可以单独调节。读取参数或开启同步选项本身不会覆盖已有的左右耳差异。
“启用自定义通透模式”控制自定义参数是否启用；耳机当前聆听模式仍需切换到**通透**，
可以使用耳柄的长按操作。工具暂不切换降噪/通透模式，也不负责蓝牙配对。

演示模式不访问真实设备：

```sh
.venv/bin/airpods-transparency gui --demo
```

指定 `gui --mac AA:BB:CC:DD:EE:FF` 时，界面自动读取参数。

## 先诊断访问权限

下列 `AA:BB:CC:DD:EE:FF` 都应替换为 `devices` 命令显示的耳机地址。

```sh
python3 -m airpods_transparency doctor --mac AA:BB:CC:DD:EE:FF
python3 -m airpods_transparency read --mac AA:BB:CC:DD:EE:FF
```

`doctor` 只读检查配对、连接、配置文件和通透数据，不会写耳机参数。
如遇 `0x0e`、授权或认证错误，按以下步骤检查 Apple DeviceID。
连接被拒绝或超时也可能是耳机未连接、其他控制程序占用通道，或固件不兼容。
系统 BlueZ 完成 GATT 服务发现后会占用 ATT 通道，此时直接 ATT 连接可返回
`Connection refused`，即使音频仍正常。本工具会按设备地址和特征 UUID
`d5621cc1-f7ab-45db-9403-9eaf744d5390` 找到服务，使用 BlueZ 已有的连接，
不要求重启蓝牙。按特征属性选择有响应/无响应写入，随后仍读回完整参数核对。

### JSON 调试入口

GUI 与 CLI 共用控制器及传输层。成功、运行错误及参数错误均可输出 JSON，失败退出码为 1：

```sh
python3 -m airpods_transparency doctor --mac AA:BB:CC:DD:EE:FF --json
python3 -m airpods_transparency read --mac AA:BB:CC:DD:EE:FF --json
python3 -m airpods_transparency --json devices
```

JSON 包含 `ok`、实际使用的 `backend`（成功读取时）、参数或结构化 `error`（类型、
消息、系统 errno、ATT 错误码）；`doctor` 失败时还保留已完成的连接/配置诊断。
所有带 `--mac` 的 CLI 操作支持 `--transport auto|bluez|att`，便于分别诊断两条路径。
`--transport att` 在 BlueZ 已接管 ATT 时失败是预期结果；通常应保留默认 `auto`。

## Apple DeviceID：一次性系统配置

LibrePods 文档说明，部分参数要求主机使用苹果的蓝牙 DeviceID。此配置适用于整个电脑
的蓝牙服务，重启会暂时断开蓝牙键盘、鼠标及耳机。

可以使用仓库提供的可回退配置工具。它在改动前保存 `/etc/bluetooth/main.conf` 原文件，
只编辑 `[General]` 下的 DeviceID，不自动重启服务、不移除任何配对。

```sh
sudo python3 tools/configure_bluez.py --enable
sudo systemctl restart bluetooth
```

或手动在 `/etc/bluetooth/main.conf` 的 `[General]` 节设置：

```ini
DeviceID = bluetooth:004C:0000:0000
```

重新连接耳机后再运行 `doctor`。耳机可能缓存主机身份，必要时在系统蓝牙设置中移除
**这一副耳机**再配对。若已连到耳机但仍返回错误，请保留错误信息；不要尝试随机句柄。

配置工具会打印备份路径。恢复主机原配置：

```sh
sudo python3 tools/configure_bluez.py --restore /etc/bluetooth/main.conf.airpods-BACKUP
sudo systemctl restart bluetooth
```

上面的备份路径是占位符，请使用工具实际输出。LibrePods 提醒，模拟苹果身份可能导致
部分固件周期性断开连接；遇到这种情况应恢复原配置。

## 命令行调节

先 `read` 查看实际值。以下是语法示例，不代表经听感验证的推荐参数：

```sh
python3 -m airpods_transparency set --mac AA:BB:CC:DD:EE:FF \
  --enabled on --amplification -0.2 --noise-reduction 0.3 --conversation-boost off
```

同一参数可以分别设置左右耳，单耳参数优先于公共参数：

```sh
python3 -m airpods_transparency set --mac AA:BB:CC:DD:EE:FF \
  --left-amplification -0.1 --right-amplification -0.2
```

支持：`--enabled on|off`、`--amplification -1..1`、`--tone -1..1`、
`--noise-reduction 0..1`、`--conversation-boost on|off`、`--own-voice 0..1`。
中间四项均支持 `--left-…` 和 `--right-…`。
这些数值是协议中的归一化值，**不是分贝**；没有承诺与苹果界面刻度完全一致。
100 字节布局没有 `own-voice` 字段，会拒绝设置它。

每次实际写入之前都会将完整原始值保存到
`${XDG_STATE_HOME:-~/.local/state}/airpods-transparency/backups/`，文件权限为 `0600`。
不变的设置不会触发写入；没有指定修改的 EQ、另一只耳朵及其他参数均逐字节保留。
写入后必须读回完全一致才报告成功。失败时会显示备份路径；此时耳机状态可能已改变，
请重新读取后再操作，工具不会反复自动写入。

```sh
python3 -m airpods_transparency backup --mac AA:BB:CC:DD:EE:FF
python3 -m airpods_transparency restore --mac AA:BB:CC:DD:EE:FF /path/to/backup.json
```

恢复也会先备份当前值。备份绑定耳机蓝牙地址，拒绝恢复到另一副耳机或不同长度的布局。

## 协议与来源

依据 LibrePods 提交 `89356c08cea3db0b6b54792d9329d09a87cc85fa`：

- [Transparency.kt](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/android/app/src/main/java/me/kavishdevar/librepods/data/Transparency.kt)：100/104 字节、little-endian float 参数布局。
- [ATTManager.kt](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/android/app/src/main/java/me/kavishdevar/librepods/bluetooth/ATTManager.kt)：通透句柄 `0x18` 与 ATT 读写。
- [Linux 文档](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/linux/README.md)：BR/EDR ATT 接入及 DeviceID 配置。

传输使用 Bluetooth L2CAP PSM `0x001f`（ATT），与切换降噪模式使用的 AACP 通道不同。
现有旧版 `hearing-aid-adjustments.py` 实际写入助听句柄，**没有直接把它当成通透工具复制**。
本项目按通透源码实现，采用 GPL-3.0-or-later，向 LibrePods 及协议研究贡献者致谢。

直接 ATT 路径使用上游公布的 `0x18` 句柄；BlueZ 路径按 UUID 发现特征，
不依赖系统生成的对象路径编号。两条路径均支持 100/104 字节布局。
检测长度、有限浮点数及字段范围，未知布局拒绝写入。没有固件白名单；
结构检查不能证明所有固件语义相同。
这是读写协议验证工具，尚不保证参数在切换设备、重连或重启耳机后如何保持。

## 开发与验证

```sh
python3 -m pip install -e '.[test,gui]'
pytest -q
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/gui_smoke.py -q
```

CI 测试参数完整性、异常响应、通知与响应分离、备份先于写入、恢复、并发修改检测、写后
验证失败等路径。GUI 冒烟测试使用模拟设备；真实耳机的授权、读写及听感需另行验证。

不需要 Apple 账户、云服务或固件提取。应用没有遥测，也不上传耳机参数。
