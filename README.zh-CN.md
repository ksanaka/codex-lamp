# Codex Lamp

[English](README.md)

Codex Lamp 把 Moonside Halo 变成 Codex 的环境状态灯。快速且故障开放的生命周期钩子只写入本地会话状态；独立守护进程维持一条蓝牙低功耗连接。多个 Codex 会话按 `input > working > idle > off` 聚合。

Codex Lamp 运行时无需网络。钩子处理不会等待蓝牙；灯具缺失、权限缺失或守护进程故障都不会中断 Codex。

## 前置条件

- macOS（首个版本不支持其他操作系统）
- Python 3.10 或更高版本，并包含 `venv` 模块
- 可通过 `codex` 命令运行的 Codex CLI
- 提供 `/plugins` 和 `/hooks` 的 Codex 使用界面
- 进行发现和实体测试时，需要已通电的 Moonside Halo 和 macOS 蓝牙权限；`test --dry-run` 不需要灯具或蓝牙

安装器会在 `~/Library/Application Support/CodexLamp/` 下创建隔离运行环境，安装 `bleak>=0.22,<2`，并在 `~/.local/bin` 中创建 `codex-lamp` 符号链接。如果 shell 的 `PATH` 尚未包含该目录，请手动加入。

## 从本地 ZIP 安装

当前支持的安装方式是本地 ZIP 加本地 Marketplace。下载
`codex-lamp-v0.1.0-local.zip`，在包含该文件的目录中打开终端并解压：

```bash
unzip codex-lamp-v0.1.0-local.zip
cd codex-lamp
./install.sh
```

脚本会检查 macOS、Python 和 Codex，创建或升级隔离运行环境，并把解压后的目录注册为 `codex-lamp-marketplace`。它不会编辑 `~/.codex/config.toml`，也不会替你安装插件或批准钩子。

请在 Codex 中明确完成其余步骤：

1. 打开 `/plugins`，选择 **Codex Lamp** Marketplace，然后安装 **Codex Lamp**。
2. 打开 `/hooks`，检查六个运行 `bash "${PLUGIN_ROOT}/scripts/hook_runner.sh"` 的条目。
3. 只有在认可该命令和当前解压内容后，才信任这些钩子。
4. 触发一次 Codex 会话事件，然后运行 `codex-lamp doctor`。

安装插件不代表信任钩子。Codex 会跳过未受信任的插件钩子；钩子定义发生变化后必须重新检查。不要用危险的信任绕过参数或手工写入批准配置来跳过此步骤。

## GitHub 发布后

Codex Lamp 目前没有 GitHub 仓库。请勿使用虚构的仓库 URL 或
Marketplace 来源；当前请使用上面的本地 ZIP 流程。真实仓库发布后，
本节将提供准确的 clone 和 `codex plugin marketplace add` 命令。当前
Marketplace 来源语法见 [Package your plugin](https://developers.openai.com/plugins/build/plugins#add-a-marketplace-from-the-cli)。

## 灯光状态

| 有效状态 | Codex 触发条件 | Halo 默认表现 |
| --- | --- | --- |
| `input` | `PermissionRequest`，或完成的回复明确要求用户输入 | 紫色常亮，RGB `200,0,255` |
| `working` | `UserPromptSubmit` 或 `PreToolUse` | 白色/海军蓝 `BEAT2` 主题 |
| `idle` | `SessionStart`，或普通完成 | 日落芒果色常亮，RGB `255,180,50` |
| `off` | 没有活动会话，或所有记录均已过期 | LED 关闭 |

并发会话中优先级最高的状态生效：`input > working > idle > off`。会话记录默认在 1,800 秒后过期。`Stop` 分类器会识别末尾的 `?`/`？` 和配置的中英文短语；无法确定的完成回复会进入 `idle`。

## 命令

```bash
codex-lamp setup
codex-lamp scan
codex-lamp status
codex-lamp status --json
codex-lamp config
codex-lamp doctor
codex-lamp doctor --json
codex-lamp test --dry-run
codex-lamp test
codex-lamp logs --lines 100
codex-lamp uninstall
```

- `setup` 仅在配置不存在时创建默认配置，不覆盖现有值。
- `scan` 会进行真实蓝牙发现并打印选中的设备。
- `status` 报告守护进程 PID/运行状态、设备选择器、活动会话数和有效状态。
- `doctor` 检查 Python、`bleak`、macOS、蓝牙、设备发现、守护进程和插件钩子文件是否可发现；它无法判断你是否已在 `/hooks` 中批准钩子，任一报告项失败时退出码非零。
- `test --dry-run` 打印所有状态命令，不访问蓝牙。
- `test` 连接真实硬件，并依次显示 `idle`、`working`、`input` 和 `off`，每个状态持续一秒。
- `logs` 打印日志目录和指定数量的最近日志行。

刚完成 setup 时，`doctor` 可能报告 `daemon: failed`：守护进程由第一个受信任的生命周期钩子启动，不由 `setup` 启动。

## 配置

列出全部支持的值、读取单项或更新单项：

```bash
codex-lamp config
codex-lamp config brightness
codex-lamp config idle_color 32,64,128
codex-lamp config device_uuid null
codex-lamp config question_markers 'please confirm,would you like,请确认,请选择'
```

| 键 | 默认值 | 格式或限制 |
| --- | --- | --- |
| `device_name_prefix` | `MOONSIDE` | 未设置 UUID 时使用的设备名前缀 |
| `device_uuid` | `null` | macOS CoreBluetooth UUID；`none` 或 `null` 可清除 |
| `idle_color` | `255,180,50` | 三个以逗号分隔的 `0..255` 整数 |
| `input_color` | `200,0,255` | 三个以逗号分隔的 `0..255` 整数 |
| `working_command` | `THEME.BEAT2.255,255,255,0,0,140,` | 原始 Moonside 主题命令；在 shell 中请加引号 |
| `brightness` | `120` | `0..120` 整数 |
| `stale_seconds` | `1800` | 正整数秒数 |
| `poll_interval` | `0.2` | 守护进程轮询间隔，正数秒 |
| `relaunch_cooldown` | `30` | 为兼容性保留的正整数；当前钩子路径不读取它 |
| `question_markers` | 默认中英文短语 | 逗号分隔；空项目会被忽略 |

配置文件位于 `~/Library/Application Support/CodexLamp/config.json`。高级用户可在 setup 前设置 `CODEX_LAMP_HOME` 改变数据根目录；Codex 与后续 CLI 命令必须使用同一个解析后的值，否则会创建彼此独立的运行环境。状态优先级固定，不能重排。

配置变更后运行：

```bash
codex-lamp doctor --json
codex-lamp status --json
codex-lamp test --dry-run
```

只有在确实要控制实体灯具时才运行 `codex-lamp test`。

## 升级

对于当前本地部署，请下载更新的本地 ZIP 版本并解压到新的
`codex-lamp` 目录。进入这个新目录后刷新隔离运行环境：

```bash
./plugins/codex-lamp/scripts/setup.sh
```

如果还需要把新的解压目录注册为本地 Marketplace 来源，请改为运行
`./install.sh`。打开 `/plugins` 更新或重新安装 Codex Lamp；必要时重启
ChatGPT 桌面应用。再次打开 `/hooks`，检查当前定义后再信任任何发生变化的
钩子。运行环境 setup 使用 `pip install --upgrade`，但会保留已有的
`config.json`。真实 GitHub 仓库发布后，本节才会加入 Git Marketplace
刷新说明。

## 卸载

先仅删除本地运行数据：

```bash
codex-lamp uninstall
```

该命令会显示准确的数据目录供确认，验证目标已经初始化且范围安全，尽可能停止经过验证的守护进程，在有界超时内尝试发送 `LEDOFF`，然后只删除这个目录。只有独立确认目标后才使用 `codex-lamp uninstall --yes`。

运行环境卸载不会删除或禁用插件、注销 Marketplace、删除解压目录，也不会删除 `~/.local/bin/codex-lamp` 符号链接。请分别完成：

1. 打开 `/plugins`，禁用或删除 **Codex Lamp**。
2. 如有需要，运行 `codex plugin marketplace remove codex-lamp-marketplace`。
3. 如果不再需要，删除仍然存在但已无用的 `~/.local/bin/codex-lamp` 符号链接和解压目录。

## 架构

```text
Codex 生命周期事件（stdin 中的 JSON）
        |
        v
故障开放钩子运行器 -> 钩子路由器 -> 加锁的逐会话 JSON 记录
                                             |
                                             v
                                      有效状态聚合
                                             |
                                             v
                                  单个持久 BLE 守护进程
                                             |
                                             v
                                Moonside Nordic UART Service
```

钩子路由器只进行本地原子文件更新和分离式守护进程启动，不执行 BLE 发现。会话文件名使用 SHA-256 摘要，写入使用临时文件替换，聚合过程由文件锁保护。守护进程持有独立进程锁，抑制重复状态写入，以有界退避重连，重连后重新应用状态，并在关闭时尝试熄灯。守护进程日志在数据根目录下轮转。

## 故障排查

| 现象 | 处理方法 |
| --- | --- |
| `codex-lamp: command not found` | 把 `~/.local/bin` 加入 `PATH`，或在解压后的 `codex-lamp` 目录中重新运行 `./plugins/codex-lamp/scripts/setup.sh`（也可运行 `./install.sh`）。 |
| `doctor` 报告 `platform: failed` | 使用 macOS；其他平台不在当前版本范围内。 |
| 缺少 `bleak` | 重新运行 `./plugins/codex-lamp/scripts/setup.sh`。 |
| `bluetooth` 失败 | 开启蓝牙，并在 macOS 系统设置中授予宿主应用或终端蓝牙权限。 |
| `device` 失败 | 给 Halo 通电并移近，运行 `codex-lamp scan`；若名称发现有歧义则配置 UUID。 |
| `daemon` 失败 | 安装并信任钩子，触发会话事件，然后查看 `codex-lamp logs --lines 100`。 |
| `hooks` 失败 | 确认插件已安装，并重新运行插件 setup 以便发现钩子文件；另行在 `/hooks` 中检查信任状态，`doctor` 无法验证批准。 |
| 状态似乎卡住 | 检查 `status --json`、并发会话和 `stale_seconds`；已结束或过期会话会自动移除。 |
| 灯具断开 | 保持守护进程运行；它会按有界退避重新扫描，并在重连后重新应用有效状态。 |
| 灯具失败但 Codex 继续工作 | 这是预期的故障开放行为；请从日志中查找硬件或运行环境错误。 |

## 安全

`codex-lamp test`、`scan`、正常受信任的钩子和卸载操作都可能访问真实蓝牙硬件。工作主题有动画且可能脉动。请提醒对闪光敏感的人，不要把本灯用作安全关键指示器，并在实体测试前先使用 `test --dry-run`。在 `/hooks` 中检查每一条钩子命令，绝不要绕过 Codex 的信任决定。卸载前务必检查命令显示的准确数据路径。

## 参与贡献

钩子必须保持故障开放，且不得包含同步 BLE 工作。修改运行时行为前先添加会失败的行为测试；自动化 BLE 覆盖应使用模拟传输层。典型开发环境如下：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ./plugins/codex-lamp pytest PyYAML
.venv/bin/python -m pytest -v
bash -n install.sh plugins/codex-lamp/scripts/*.sh
git diff --check
```

插件清单、Marketplace、技能或钩子的变更还应通过当前 Codex 插件和技能验证器。实体硬件测试必须经过明确批准，并在独立的 macOS 验收步骤中进行。

## 许可证与署名

新项目代码采用 [MIT License](LICENSE)。Moonside 协议和守护进程方法参考了 [bobek-balinek/claude-lamp](https://github.com/bobek-balinek/claude-lamp)，其 MIT 声明完整保存在 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
