# 开发说明

## 本地开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test,gui]'
pytest -q
QT_QPA_PLATFORM=offscreen python -m pytest tests/gui_smoke.py -q
```

协议和服务测试不需要真实耳机。GUI 冒烟测试使用模拟设备，验证滑条、左右耳同步、模式选择及预设流程。实机验证范围见 [硬件记录](hardware-validation.md)。

修改后台服务代码后，运行 `airpods-control service stop --json`，让下一次请求启动新进程。直接前台运行服务可使用 `python -m airpods_transparency.service`。

## 结构

| 模块 | 职责 |
| --- | --- |
| `gui.py` | Qt 窗口、托盘及界面状态 |
| `cli.py` | CLI 和 JSON 输出 |
| `service.py` | 用户级服务、请求串行化和持续设备连接 |
| `aacp.py` | 电量、元数据、佩戴通知和聆听模式 |
| `bluez.py` / `transport.py` | BlueZ GATT 与直接 ATT 传输 |
| `settings.py` / `controller.py` | 参数校验、保留未编辑值、备份和写后验证 |
| `profiles.py` | 可移植的声音预设 |

GUI、托盘和 CLI 通过 JSON Lines 调用同一用户级服务。Unix socket 位于用户私有运行目录，目录权限为 `0700`，socket 权限为 `0600`；两端检查对方的用户身份。服务没有 HTTP 监听端口。

AACP 连接持续接收通知，断开后采用退避重试。通透参数操作串行执行；发送写请求后不会自动重发，以免在结果未知时重复修改设备。

## 协议来源

协议参考 LibrePods 提交 `89356c08cea3db0b6b54792d9329d09a87cc85fa`：

- [AAP Definitions](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/docs/AAP%20Definitions.md)：AACP 握手、电量、设备信息和聆听模式。
- [Transparency.kt](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/android/app/src/main/java/me/kavishdevar/librepods/data/Transparency.kt)：通透参数布局。
- [ATTManager.kt](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/android/app/src/main/java/me/kavishdevar/librepods/bluetooth/ATTManager.kt)：ATT 读写。
- [Linux 文档](https://github.com/librepods-org/librepods/blob/89356c08cea3db0b6b54792d9329d09a87cc85fa/linux/README.md)：DeviceID 配置。

AACP 使用 L2CAP PSM `0x1001`。直接 ATT 路径使用 PSM `0x001f`、通透值句柄 `0x18`；BlueZ 路径按 UUID `d5621cc1-f7ab-45db-9403-9eaf744d5390` 发现特征，使用系统已有的连接，避免争抢 ATT 通道。

通透参数支持 100 / 104 字节的 little-endian float 布局。100 字节布局没有自己声音增音字段。未知长度、非有限浮点数或异常字段范围会被拒绝；未编辑的 EQ 等字节保留原值。程序不操作助听器值句柄 `0x2a`、听力图或固件。

备份保存完整原始参数，并绑定设备地址；预设只保存可编辑的标量参数及可选聆听模式。恢复备份前同样会保存当前原值。

## 当前边界

型号和固件兼容性需要分别验证。通过结构校验和读回验证，并不能证明所有固件上的听感完全相同；跨设备切换、蓝牙重连和耳机重启后的参数保持行为也需要进一步测试。

尚未实现开机自启、摘耳自动暂停、全局快捷键、系统 EQ、跨苹果设备自动切换、查找网络或固件更新。佩戴状态目前只用于诊断。
