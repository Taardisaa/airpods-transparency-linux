# 命令行参考

安装并激活虚拟环境后使用 `airpods-control`。`airpods-transparency` 是兼容别名，也可以使用 `python -m airpods_transparency`。

下文的 `AA:BB:CC:DD:EE:FF` 请替换为 `airpods-control devices` 列出的耳机地址。

## 设备和状态

```bash
airpods-control devices --json
airpods-control status --mac AA:BB:CC:DD:EE:FF --json
airpods-control watch --mac AA:BB:CC:DD:EE:FF --interval 2 --json
airpods-control doctor --mac AA:BB:CC:DD:EE:FF --json
```

- `devices`：列出 BlueZ 中已知、名称含 AirPods 的设备；配对请使用系统蓝牙设置。
- `status`：电量、模式、型号、固件及控制连接状态。未报告的电量为 `null`。
- `watch`：有变化时输出一行 JSON，Ctrl+C 结束。轮询间隔可设为 0.2～60 秒。
- `doctor`：诊断配对、连接、DeviceID、后台服务和声音参数访问。

`connected` 表示 AirPods 控制通道状态。`ears` 是协议中的主耳 / 次耳佩戴状态，不保证对应左耳 / 右耳；当前应用不会据此自动暂停播放。

## 聆听模式

```bash
airpods-control mode anc --mac AA:BB:CC:DD:EE:FF --json
```

| 值 | 模式 |
| --- | --- |
| `anc` | 主动降噪 |
| `transparency` | 通透 |
| `adaptive` | 自适应 |
| `off` | 关闭 |

选择 `off` 时会先启用耳机的“关闭聆听模式”选项。工具会等待设备确认；没有收到确认时返回错误。具体可用模式取决于型号和固件。

## 通透参数

```bash
airpods-control read --mac AA:BB:CC:DD:EE:FF --json

airpods-control set --mac AA:BB:CC:DD:EE:FF \
  --enabled on --amplification -0.2 --noise-reduction 0.3 \
  --conversation-boost off --json
```

上面的数值只用于演示语法。参数含义见 [README](../README.md#通透参数)。

| 参数 | 可用值 |
| --- | --- |
| `--enabled` | `on` / `off` |
| `--amplification` | −1～1 |
| `--tone` | −1～1 |
| `--noise-reduction` | 0～1 |
| `--conversation-boost` | `on` / `off` |
| `--own-voice` | 0～1，需设备支持 |

`amplification`、`tone`、`noise-reduction` 和 `conversation-boost` 均支持 `--left-…`、`--right-…` 前缀。公共参数作用于两耳，单耳参数优先：

```bash
airpods-control set --mac AA:BB:CC:DD:EE:FF \
  --amplification -0.2 --right-amplification -0.1 --json
```

未指定的参数保持原值。GUI 中的“同步左右耳”只控制界面编辑，CLI 通过公共参数表达两耳同步修改。

## 配置预设

```bash
airpods-control preset save "Office" --mac AA:BB:CC:DD:EE:FF --json
airpods-control preset list --json
airpods-control preset apply "Office" --mac AA:BB:CC:DD:EE:FF --json
airpods-control preset delete "Office" --json
```

保存的是耳机当前已应用的声音参数，以及已知的当前聆听模式。添加 `--without-mode` 可只保存声音参数。同名预设会被覆盖。

预设可在设备间使用，应用时保留目标设备的 EQ。如果声音设置已应用，但模式切换失败，错误信息会明确说明部分成功状态，并提供声音原值备份路径。

## 备份和恢复

```bash
airpods-control backup --mac AA:BB:CC:DD:EE:FF --json
airpods-control restore --mac AA:BB:CC:DD:EE:FF /path/to/backup.json --json
```

每次实际写入声音参数前都会自动备份；`backup` 可主动生成一份。恢复操作也会先备份当前值。备份绑定设备地址，拒绝恢复到另一副耳机或不同长度的参数布局。

## 界面和后台服务

```bash
airpods-control gui --mac AA:BB:CC:DD:EE:FF
airpods-control gui --tray --mac AA:BB:CC:DD:EE:FF
airpods-control gui --demo

airpods-control service start --json
airpods-control service status --json
airpods-control service stop --json
```

演示模式不访问真实耳机。`service status` 不会主动启动服务，其结果包含版本、PID 和日志路径。其他设备操作会按需启动后台服务。

服务只为 BlueZ 已连接的耳机建立控制通道，不负责配对或主动抢占其他设备的音频。退出 GUI 后，服务仍可供 CLI 使用。

## JSON 和错误

`--json` 可放在命令前，或放在支持它的子命令后。成功结果包含 `ok: true`；参数错误或操作失败时退出码为 1，并返回结构化错误：

```json
{
  "ok": false,
  "error": {
    "type": "ConnectionRefusedError",
    "message": "Connection refused",
    "errno": 111,
    "att_code": null
  },
  "diagnostics": {}
}
```

`read` 的 JSON 结果还包含实际使用的 `backend` 和 `values`。`doctor` 失败时会保留已完成的诊断信息。设备状态中的 `error` 可以描述控制通道断开；这不等同于状态查询请求本身失败。

`read`、`set`、`backup`、`restore`、`doctor`、`preset` 和 `gui` 支持 `--transport auto|bluez|att`。通常保持 `auto`；只有排查传输问题时才需要指定。BlueZ 已接管 ATT 时，强制使用 `att` 可能返回连接拒绝。

## 文件位置

| 内容 | 默认位置 |
| --- | --- |
| 配置预设 | `~/.config/airpods-transparency/presets.json` |
| 声音参数备份 | `~/.local/state/airpods-transparency/backups/` |
| 服务日志 | `~/.local/state/airpods-transparency/service.log` |

支持 `XDG_CONFIG_HOME` 和 `XDG_STATE_HOME`。日志按 1 MiB 轮转，保留两份历史。备份和预设文件权限为 `0600`。
