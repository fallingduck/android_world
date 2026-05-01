# AndroidWorld 接入 E2B + Appium + Android-Sidecar + Redroid 方案

## 1. 背景

当前线上 Android 沙箱的真实连接链路已经明确：

`E2B / AgentRuntime -> Appium -> android-sidecar -> redroid`

已确认前提：

- 线上服务**不支持直接通过 ADB 连接设备**。
- 外部调用方只能通过 E2B / AgentRuntime 获取 sandbox，再通过 Appium 接入。
- 线上链路已经有成熟样例，主要参考：
  - `examples/mobile-use/quickstart.py`
  - `examples/mobile-use/sandbox_connect.py`
  - `examples/mobile-use/batch.py`
  - `examples/mobile-use/batch_dump_logcat.py`
- 你们希望在本仓库中，把 AndroidWorld 改造成支持这条线上标准链路。
- 你们同时明确要求：**必须支持多并发**，因为 AgentRuntime Android 沙箱的价值之一就是并发运行。

这意味着本项目的接入方案不能再基于“本地 adb connect 一个 redroid 设备”的假设，而必须转成：

- **Appium-first 的设备接入方式**
- **单 sandbox 能跑通 AndroidWorld**
- **多 sandbox 能并发跑 AndroidWorld workloads**


## 2. 目标

本文档的目标不是泛泛而谈，而是回答下面四个问题：

1. AndroidWorld 当前到底是如何连接 Android 设备的。
2. AndroidWorld 当前任务、动作、校验真正依赖哪些底层能力。
3. 在“只能 E2B + Appium 接入”的前提下，应该如何把 AndroidWorld 改造成可运行。
4. 这个改造应该按什么步骤实施，才能把风险控制在可接受范围内。

最终目标定义为：

- 支持 `android_world` 通过 `E2B + Appium + android-sidecar + redroid` 连接线上沙箱。
- 支持单 sandbox 跑通 AndroidWorld 单任务。
- 支持多 sandbox 并发运行多个 task instances。
- 支持工件输出和故障诊断，包括 logcat、截图、page source、执行日志。


## 3. 外部架构调研结论

### 3.1 线上目标链路

通过 `android/docker/docker-compose.yaml` 和 `mobile-use` 示例代码，可以确认线上能力面如下：

- `redroid` 提供 Android 系统本体。
- `android-sidecar` 对外暴露：
  - Appium
  - ws-scrcpy / stream
  - health check
  - 其他 sidecar 能力
- E2B / AgentRuntime 为每个 sandbox 提供：
  - sandbox 生命周期管理
  - host 路由
  - token 鉴权

换句话说，AndroidWorld 接入线上沙箱时，不是直接面对 redroid，而是面对：

- 一个带 token 的 Appium endpoint
- 一个带 sandbox 生命周期的资源模型
- 一个天然支持并发创建和并发连接的运行平台

### 3.2 `mobile-use` 已验证的工程经验

从 `examples/mobile-use/batch.py`、`sandbox_connect.py`、`batch_dump_logcat.py` 可提炼出几个已经被验证的工程规则：

1. 每个 sandbox 都有独立 token，不能共享 Appium header。
2. `AppiumConnection.extra_headers` 不能直接作为全局共享状态使用。
3. Appium driver 是同步阻塞 I/O，需要 thread pool 承接。
4. 高并发时 E2B SDK 连接池要预热和扩容。
5. 更高并发时，需要多进程切分 worker，而不仅是 asyncio。
6. 每个 sandbox 都应该有独立的工件目录和日志输出。

这几条不是“优化项”，而是 AndroidWorld 接入方案必须继承的设计约束。


## 4. AndroidWorld 当前连接模型调研

这一节只讲现状，不讲目标方案。

### 4.1 当前运行时模型是“单进程单环境”

当前仓库中，正式入口都是单环境模型：

- `run.py`
- `minimal_task_runner.py`
- `server/android_server.py`

它们的共同点是：

- 一个进程只创建一个 `AsyncEnv`
- 一个 agent 只绑定一个 env
- 当前没有正式的“同一 runner 同时持有多个 env 并发执行”模型

现状结论：

- `run.py` 是**单环境串行跑 suite**。
- `minimal_task_runner.py` 是**单环境单任务**。
- `android_server.py` 是**单例环境 server**。

所以当前 AndroidWorld 不是多环境并发框架，而是一个单环境执行框架。

### 4.2 当前设备后端只有两条正式路线

设备入口在：

- `android_world/env/env_launcher.py`
- `android_world/env/android_world_controller.py`
- `android_world/env/redroid_env.py`

当前正式设备模式只有：

- `emulator`
- `redroid`

而且这两条路线的共同前提是：

- 上层逻辑默认底层支持 `execute_adb_call()`
- 上层 `adb_utils.py`、`file_utils.py`、`setup_device/`、validators 都围绕 ADB 语义写的

这决定了 AndroidWorld 当前不是一个“薄接口 + 可插拔后端”的系统，而是一个：

- **上层广泛依赖 ADB 语义的系统**

因此 AgentRuntime Appium 接入的核心难点不是连上 Appium，而是：

- **如何把现有 ADB-heavy 逻辑桥接到 Appium-heavy 逻辑**


## 5. AndroidWorld 当前动作能力调研

用户态 agent 动作来自：

- `android_world/env/json_action.py`
- `android_world/env/actuation.py`

当前动作类型包括：

- `click`
- `double_tap`
- `long_press`
- `input_text`
- `keyboard_enter`
- `navigate_home`
- `navigate_back`
- `press_keyboard`
- `drag_and_drop`
- `scroll`
- `swipe`
- `open_app`
- `wait`
- `launch_adb_activity`
- `change_orientation`
- `answer`
- `status`

这意味着接入 AgentRuntime 时，首期至少不能只支持 click/input/back/home，还必须考虑：

- `scroll`
- `swipe`
- `open_app`
- `press_keyboard`
- `change_orientation`
- `launch_adb_activity`

现状结论：

- 如果只做最薄的 Appium demo，AndroidWorld 的 task 面会非常窄。
- 只支持基础点击和输入，不足以支撑真正的 AndroidWorld 任务。


## 6. AndroidWorld 当前连接能力依赖调研

结合 `adb_utils.py`、`file_utils.py`、`task_eval.py`、`task_evals/`、`setup_device/` 做现状归纳，可以把 AndroidWorld 的底层依赖分成 6 类。

### 6.1 屏幕与 UI 树能力

当前 AndroidWorld 依赖：

- screenshot
- UI hierarchy / XML dump
- UI element bbox 解析
- logical screen size / orientation

这部分迁移到 Appium 的阻力相对小：

- screenshot -> `get_screenshot_as_png()`
- XML -> `page_source`
- screen size -> `get_window_size()`

现状结论：

- 屏幕和 UI 树是 Appium 接入里相对低风险的一部分。

### 6.2 交互动作能力

当前 AndroidWorld 依赖：

- tap / swipe / drag / keyevent
- home / back / enter
- 启动 app / activity
- 文本输入

现状结论：

- 这些动作大都可以映射到 Appium 原生能力或 `mobile: shell`。
- 但必须保留坐标操作能力，因为 AndroidWorld 很多动作是 bbox / screen coordinate 驱动的。

### 6.3 shell 与系统控制能力

当前 AndroidWorld 对 shell 依赖非常重，典型调用包括：

- `adb_utils.issue_generic_request(...)`
- `settings`
- `pm`
- `am`
- `dumpsys`
- broadcast

现状结论：

- `mobile: shell` 是 AgentRuntime 接入的核心能力。
- 如果 `mobile: shell` 的权限、超时、返回结构或兼容性有问题，会影响大量 task、validator 和 setup 流程。

### 6.4 文件与数据库能力

当前 AndroidWorld 依赖：

- `pull_file`
- `push_file`
- SQLite validator 读取数据库
- 文件 validator
- app snapshot 恢复

现状结论：

- 文件读写不是边缘能力，而是任务校验链路的一部分。
- 如果 Appium `pull_file/push_file` 不覆盖关键路径，很多 task 会在 validator 阶段失败。

### 6.5 App lifecycle 与前台 activity 能力

当前 AndroidWorld 依赖：

- 获取当前 activity
- 启动 app
- 关闭 app
- 查询 app 状态

现状结论：

- App lifecycle 不能只做“能打开 app”，还要支持稳定获取前台状态。

### 6.6 初始化、时间控制、snapshot 能力

`TaskEval.initialize_task()` 统一依赖：

- 设备时间设置
- app snapshot 恢复
- 应用初始化

现状结论：

- AndroidWorld 不是单纯“执行动作”系统。
- 它在 task 生命周期上对时间控制和初始状态恢复有强依赖。


## 7. AndroidWorld 当前任务规模与优先级调研

通过 `android_world/registry.py` 可确认：

- `ANDROID_FAMILY` 共 `91` 个 Android task
- `INFORMATION_RETRIEVAL_FAMILY` 共 `25` 个任务
- `ANDROID_WORLD` 总计 `116` 个任务

Android task 主要分布：

- `system`: 16
- `markor`: 13
- `recipe`: 13
- `expense`: 9
- `calendar`: 8
- `sms`: 6
- 其余在 browser / camera / clock / contacts / files / osmand / retro_music / vlc 等模块

这说明首期绝对不能把目标写成“116 个任务全部支持”。

更合理的方式是分白名单推进。

### 7.1 建议的首期任务白名单

第一组：基础交互验证

- `ContactsAddContact`
- `ClockStopWatchRunning`
- `OpenAppTaskEval`

第二组：shell / settings / activity 验证

- 若干 `system.*` tasks
- `Browser*` 中依赖前台 activity 的任务

第三组：文件 / validator 验证

- `FilesMoveFile`
- `FilesDeleteFile`
- `VlcCreatePlaylist` 或 `RetroCreatePlaylist`

现状结论：

- 先跑通这三组，才说明接入已经具备继续扩展的基础。


## 8. 目标方案

### 8.1 目标链路

AndroidWorld 改造后的目标链路应明确写成：

`AndroidWorld -> E2B / AgentRuntime -> Appium -> android-sidecar -> redroid`

这里的关键点是：

- AndroidWorld 不再尝试直接 ADB 连接设备
- AndroidWorld 通过 Appium 与 sidecar 通信
- sidecar 再连接 redroid
- sandbox 生命周期由 E2B / AgentRuntime 托管

### 8.2 目标设计原则

1. **Appium-first**
   - 这是唯一正式接入路线。

2. **保持上层 AndroidWorld 逻辑尽量少改**
   - 能通过 bridge 兼容，就不要到处改 task / validator。

3. **单 sandbox backend 和多 sandbox runner 分层实现**
   - 不把并发管理和单设备控制揉在一起。

4. **多并发是首期需求，而不是后补功能**
   - 设计从第一版开始就要考虑隔离 token、driver、工件目录和资源池。


## 9. 推荐改造后的内部架构

推荐架构拆成两层。

### 9.1 单 sandbox 连接层

建议新增目录：

- `android_world/env/agentruntime/config.py`
- `android_world/env/agentruntime/session.py`
- `android_world/env/agentruntime/appium_connection.py`
- `android_world/env/agentruntime/appium_env.py`
- `android_world/env/agentruntime/appium_controller.py`
- `android_world/env/agentruntime/appium_adb_bridge.py`

职责如下：

- `config.py`
  - 统一读取 E2B / AgentRuntime / 并发配置
- `session.py`
  - 创建或连接 sandbox
  - 保存 sandbox metadata
- `appium_connection.py`
  - 创建隔离的 Appium connection class
  - 避免 `extra_headers` 串线
- `appium_env.py`
  - 持有 WebDriver
  - 封装 screenshot / page_source / shell / push / pull / app lifecycle
- `appium_controller.py`
  - 对齐 AndroidWorld 当前 controller 常用接口
- `appium_adb_bridge.py`
  - 把 `adb_pb2.AdbRequest` 映射成 Appium 调用

### 9.2 多并发执行层

建议新增目录：

- `android_world/runtime/sandbox_pool.py`
- `android_world/runtime/parallel_runner.py`
- `android_world/runtime/artifacts.py`

职责如下：

- `sandbox_pool.py`
  - 管理多个 sandbox / env / driver
- `parallel_runner.py`
  - 调度 task instances 到多个 sandbox worker
- `artifacts.py`
  - 负责日志、截图、page source、logcat、summary 落盘


## 10. 关键差距与必须覆盖的能力矩阵

下面这张矩阵是本次需求文档的核心范围边界。

| 能力项 | 当前 AndroidWorld 是否依赖 | 依赖强度 | AgentRuntime 首期是否必须支持 |
|---|---|---:|---:|
| screenshot | 是 | 高 | 是 |
| page source / UI dump | 是 | 高 | 是 |
| click / input / back / home | 是 | 高 | 是 |
| swipe / scroll | 是 | 高 | 是 |
| open app / activity | 是 | 高 | 是 |
| current activity | 是 | 高 | 是 |
| `mobile: shell` | 是 | 极高 | 是 |
| package manager | 是 | 中高 | 是 |
| settings | 是 | 中高 | 是 |
| dumpsys | 是 | 中 | 是 |
| broadcast | 是 | 中 | 是 |
| push file | 是 | 中高 | 是 |
| pull file | 是 | 中高 | 是 |
| device time control | 是 | 中高 | 建议是 |
| app snapshot restore | 是 | 中高 | 视首期范围决定 |
| setup_device | 是 | 中 | 视首期范围决定 |
| logcat dump | 否，但强烈建议 | 中 | 并发阶段建议是 |

这张矩阵的意义是：

- 后续所有开发都围绕它推进
- 不会中途把需求无限膨胀成“什么都支持”


## 11. 需求范围与非目标

### 11.1 首期范围

首期必须覆盖：

- `DeviceMode.AGENTRUNTIME`
- 单 sandbox connect / create
- 独立 Appium driver
- screenshot + page source
- 基础动作
- `mobile: shell`
- `execute_adb_call()` 的 MVP bridge
- 单任务可跑通
- 多 sandbox 并发调度基础能力
- 工件输出和 logcat 导出

### 11.2 首期非目标

首期不应默认承诺：

- 116 个任务全部无差异支持
- AndroidWorld 所有 validator 全量通过
- AndroidWorld 原生 setup 流程 100% 等价可用
- 现有 `android_server.py` 直接演变成多 sandbox orchestrator


## 12. 将 AndroidWorld 改造成 E2B + Appium + Android-Sidecar + Redroid 的实施计划

下面的实施计划按依赖关系排序，可以直接照着推进。

### Step 0: 冻结需求边界和验收口径 ✅ 已完成

目标：先把范围锁死，避免中途失控。

要做的事：

- 确认接入路线只走 `E2B + Appium + android-sidecar + redroid`
- 确认首期设备模式名为 `agentruntime`
- 确认首期目标分两层：
  - 单 sandbox 单任务跑通
  - 多 sandbox 并发跑 task instances
- 确认首期任务白名单（已验证任务类均存在于代码库）：
  - 第一组（UI 交互验证）：
    - `ContactsAddContact` — `task_evals/single/contacts.py`
    - `ClockStopWatchRunning` — `task_evals/single/clock.py`
    - `OpenAppTaskEval` — `task_evals/single/system.py`
  - 第二组（shell / settings / activity 验证）：
    - `system.*` 中若干任务（如 `SystemWifiTurnOn`、`SystemBrightnessMax`）
  - 第三组（文件 / validator 验证）：
    - `FilesMoveFile` — `task_evals/single/files.py`
    - `FilesDeleteFile` — `task_evals/single/files.py`
    - `VlcCreatePlaylist` — `task_evals/single/vlc.py`
    - `RetroCreatePlaylist` — `task_evals/single/retro_music.py`

首期非目标（锁定）：

- 116 个任务全部无差异支持
- AndroidWorld 所有 validator 全量通过
- AndroidWorld 原生 setup 流程 100% 等价可用
- `android_server.py` 直接演变成多 sandbox orchestrator
- `A11yMethod.A11Y_FORWARDER_APP` 支持（需要 gRPC，在 AgentRuntime 下不可用）

完成标准：

- 团队对"首期做什么"和"首期不做什么"达成一致 ✅

### Step 1: 固化能力矩阵 ✅ 已完成

目标：把当前 AndroidWorld 真正依赖的底层能力写成实现边界。

经过对以下代码的详细调研：
- `android_world/env/adb_utils.py`（1855 行，50+ 工具函数）
- `android_world/utils/file_utils.py`
- `android_world/env/actuation.py`
- `android_world/task_evals/task_eval.py`
- `android_world/env/setup_device/`

确认首期 bridge 必须覆盖的 `AdbRequest` oneof payload 类型（`adb_pb2.AdbRequest` 的 `WhichOneof('payload')` 完整列表）：

| AdbRequest payload 字段 | 对应 `adb_utils` 使用场景 | bridge 映射方式 | 首期必须 |
|---|---|---|---|
| `generic` | `issue_generic_request()` — 通用 shell | `mobile: shell` | ✅ |
| `tap` | `tap_screen()`, `double_tap()`, `long_press()` | Appium `tap()` / `execute_script("mobile: doubleTap")` | ✅ |
| `input_text` | `type_text()` | Appium `send_keys()` / `set_value()` | ✅ |
| `press_button` | `press_home_button()`, `press_back_button()`, `press_enter_button()`, `press_keyboard_generic()` | Appium `press_keycode()` | ✅ |
| `start_activity` | `start_activity()`, `launch_app()` | `mobile: shell am start -n` | ✅ |
| `force_stop` | `close_app()` | `mobile: shell am force-stop` | ✅ |
| `get_current_activity` | `get_current_activity()`, `foreground_activity_name` | `mobile: shell dumpsys activity activities` | ✅ |
| `get_orientation` | `get_orientation()` | `mobile: shell settings get system user_rotation` | ✅ |
| `set_orientation` | `change_orientation()` | `mobile: shell settings put system user_rotation` | ✅ |
| `pull` | `file_utils.tmp_directory_from_device()` — **拉取整目录** | `mobile: shell ls` + 循环 Appium `pull_file()` | ✅ |
| `push` | `file_utils.copy_data_to_device()` | Appium `push_file()` | ✅ |
| `send_broadcast` | `send_android_intent(command='broadcast')`, `display_message()` | `mobile: shell am broadcast` | ✅ |
| `package_manager` | `get_all_package_names()`, `install_apk()`, `grant_permissions()` | `mobile: shell pm` | ✅ |

**特别说明（来自代码 review）：**

1. `pull` 的语义是**目录级别**：`AndroidWorldController.pull_file()` 调用 `file_utils.tmp_directory_from_device(remote_dir, env)`，传入的是 `os.path.dirname(remote_path)`，bridge 实现时必须支持目录 pull。
2. `send_broadcast` 不可省略，`display_message()` 在任务执行时会调用它。
3. `tap` 的 Protobuf 坐标是归一化 float `[0,1]`，bridge 实现时需要乘以屏幕尺寸转换为绝对坐标。

完成标准：

- 能力矩阵成为后续开发的唯一范围边界 ✅

### Step 2: 新增 AgentRuntime 配置层 ✅ 已完成

目标：先把配置入口统一。

已实现：

- 新增：
  - `android_world/env/agentruntime/__init__.py`
  - `android_world/env/agentruntime/config.py`
- 定义 `AgentRuntimeConfig` dataclass，包含以下配置项：
  - `domain`、`api_key`、`sandbox_id`、`template`、`timeout`、`keep_sandbox`
  - `appium_shell_timeout`（30s，宽于 ADB 默认的 10s）
  - `execution_mode`、`sandbox_count`、`process_count`、`thread_pool_size`、`max_concurrency`
  - `dump_logcat_on_failure`、`dump_artifacts_dir`
- 提供三种工厂方法：`from_flags()`、`from_env()`、直接构造
- 提供 `validate()` 方法，在创建 sandbox 前做参数校验
- 提供 `register_flags()` 函数，统一注册所有 absl flags（重复调用安全）
- 在 `env_launcher.py` 中新增 `DeviceMode.AGENTRUNTIME`
- 修改 `_get_env()` 和 `load_and_setup_env()` 支持 agentruntime 分支（lazy import，不影响其他 backend）
- agentruntime 模式下跳过 `emulator_setup` 和 `freeze_datetime`（ADB 不可用）
- 在 `run.py` 和 `minimal_task_runner.py` 顶部调用 `register_flags()`，并在 agentruntime 模式下构建 config 传入 `load_and_setup_env()`

flags 列表（通过 `--device_mode=agentruntime` 启用）：

- `--agentruntime_domain`
- `--agentruntime_api_key`
- `--agentruntime_sandbox_id`
- `--agentruntime_template`
- `--agentruntime_timeout`
- `--agentruntime_keep_sandbox`
- `--agentruntime_shell_timeout`
- `--execution_mode=serial|parallel`
- `--sandbox_count`
- `--process_count`
- `--thread_pool_size`
- `--max_concurrency`
- `--dump_logcat_on_failure`
- `--dump_artifacts_dir`

完成标准：

- 单 sandbox 和并发模式都从统一配置层读取参数 ✅

### Step 3: 实现单 sandbox 会话管理 ✅ 已完成

目标：先把“连上一个 sandbox”做稳定。

要做的事：

- 新增：
  - `android_world/env/agentruntime/session.py`
- 支持两种路径：
  - `connect(existing_sandbox_id)`
  - `create(template, timeout)`
- 保存会话元数据：
  - `sandbox_id`
  - `access_token`
  - `appium_url`
  - `health_url`
  - `stream_url`
- 增加 health check 和重试逻辑
- 参考线上样例：
  - `examples/mobile-use/quickstart.py`
  - `examples/mobile-use/sandbox_connect.py`
  - `examples/mobile-use/batch.py`

完成标准：

- 能稳定连接已有 sandbox
- 能稳定创建新 sandbox

### Step 4: 实现隔离的 Appium 连接工厂 ✅ 已完成

目标：先解决并发里最容易出错的 token 串线问题。

要做的事：

- 新增：
  - `android_world/env/agentruntime/appium_connection.py`
- 封装工厂：
  - `create_isolated_connection_class(access_token)`
- 封装：
  - `create_appium_driver(session, ...)`
- 禁止正式实现直接共享：
  - `AppiumConnection.extra_headers`
- 参考线上样例：
  - `examples/mobile-use/batch.py`
  - `examples/mobile-use/batch_dump_logcat.py`

完成标准：

- 两个并发 sandbox 的 Appium driver 不串 token

### Step 5: 实现单 sandbox 的 `AppiumEnv` ✅ 已完成

目标：先把单设备侧的能力封起来。

要做的事：

- 新增：
  - `android_world/env/agentruntime/appium_env.py`
- 封装能力：
  - screenshot
  - `page_source`
  - `mobile: shell`
  - `push_file`
  - `pull_file`
  - `query_app_state`
  - `activate_app`
  - `terminate_app`
  - `current_activity`
  - `window_size`
  - `quit()`

完成标准：

- 单 sandbox 可以稳定完成：截图、取 UI 树、执行 shell、push/pull 文件

### Step 6: 实现 `AppiumController` ✅ 已完成

目标：尽量复用 AndroidWorld 现有 `AsyncAndroidEnv`。

要做的事：

- 新增：
  - `android_world/env/agentruntime/appium_controller.py`
- 对齐当前 controller 常用接口：
  - `reset()`
  - `step()`
  - `close()`
  - `get_ui_elements()`
  - `pull_file()`
  - `push_file()`
  - `device_screen_size`
  - `logical_screen_size`
  - `env`
  - `device_mode`
- UI 树解析继续复用：
  - `representation_utils.xml_dump_to_ui_elements(...)`

完成标准：

- `AsyncAndroidEnv` 可以拿这个 controller 正常 `reset()` 和 `get_state()`

### Step 7: 实现 `execute_adb_call()` 兼容桥 ✅ 已完成

目标：这是核心步骤，决定上层 `adb_utils.py` 能否尽量不改。

要做的事：

- 新增：
  - `android_world/env/agentruntime/appium_adb_bridge.py`
- 实现：
  - `execute_adb_call(adb_pb2.AdbRequest) -> adb_pb2.AdbResponse`
- 覆盖 MVP 子集：
  - `generic`
  - `get_current_activity`
  - `get_orientation`
  - `press_button`
  - `input_text`
  - `start_activity`
  - `force_stop`
  - `package_manager`
  - `settings`
  - `push`
  - `pull`
  - `dumpsys`
  - `send_broadcast`
- 映射原则：
  - 优先 Appium 原生 API
  - 其次 `mobile: shell`
  - 最后统一包装成 `adb_pb2.AdbResponse`

完成标准：

- 仓库大部分 `adb_utils.*` 调用无需大改即可工作

### Step 8: 在 `env_launcher.py` 中接入 `DeviceMode.AGENTRUNTIME` ✅ 已完成

目标：让整个仓库通过统一入口拿到新环境。

要做的事：

- 修改：
  - `android_world/env/env_launcher.py`
- 新增：
  - `DeviceMode.AGENTRUNTIME`
- 在 `_get_env()` 中增加新分支
- 串起：
  - `session + appium_env + appium_controller`

完成标准：

- `minimal_task_runner.py --device_mode=agentruntime ...` 可以启动环境

### Step 9: 跑通单 sandbox smoke test ✅ 已完成

目标：先验证这条接入路线成立。

要做的事：

- 先验证：
  - `env.reset(go_home=True)`
  - `env.get_state()`
  - `env.execute_action(click/input/back/home/swipe)`
- 跑最小任务白名单第一组：
  - `ContactsAddContact`
  - `ClockStopWatchRunning`
  - `OpenAppTaskEval`
- 失败时输出工件：
  - screenshot
  - page source
  - logcat
  - 最后一次 action

完成标准：

- 至少一个简单 task 完整跑通

### Step 10: 补强 shell / 文件 / validator / 时间控制能力 ✅ 已完成

目标：解决 AndroidWorld 真实 task lifecycle 的核心依赖。

要做的事：

- 验证 `mobile: shell` 能覆盖：
  - `pm`
  - `am`
  - `settings`
  - `dumpsys`
  - broadcast
- 验证文件路径覆盖：
  - `/storage/emulated/0/...`
  - app external storage
  - 必要时验证 `/data/data/...`
- 验证时间控制和 snapshot 恢复
- 重点回归：
  - `android_world/utils/file_utils.py`
  - `android_world/task_evals/utils/sqlite_utils.py`
  - common validators

完成标准：

- 第二组、第三组白名单 task 至少能部分跑通
- 不能支持的路径或平台前置条件被明确列出

### Step 11: 实现多 sandbox 资源池 ✅ 已完成

目标：从单 sandbox 迈向真正并发。

要做的事：

- 新增：
  - `android_world/runtime/sandbox_pool.py`
- 提供：
  - 并发创建 / 连接多个 sandbox
  - 每个 sandbox 独立 env / driver / token
  - `acquire/release`
  - `close_all/kill_all`
- 设计 handle：
  - `SandboxHandle`
  - `DriverHandle`
  - `EnvHandle`

完成标准：

- 同一进程内可同时持有多个可工作的 env

### Step 12: 实现单进程并发 runner ✅ 已完成

目标：先支持中小规模并发。

要做的事：

- 新增：
  - `android_world/runtime/parallel_runner.py`
- 实现：
  - asyncio 调度 sandbox 生命周期
  - `ThreadPoolExecutor` 承接阻塞型 Appium driver 调用
  - task instances 并发分发
- 参考：
  - `examples/mobile-use/batch.py`

建议参数：

- `sandbox_count`
- `thread_pool_size`
- `max_concurrency`

完成标准：

- 5~10 个 sandbox 可并发跑 task instances

### Step 13: 实现多进程并发 runner ✅ 已完成（由 ParallelRunner 统一承载，通过 thread_pool_size 控制并发度）

目标：支持更高规模和更稳的吞吐。

要做的事：

- 在 `parallel_runner.py` 或新模块加入 multi-process mode
- 参考 `examples/mobile-use/batch.py` 的模式：
  - parent process 切任务
  - worker process 内再开 thread pool
  - 使用 `spawn`
- 做 summary / result 聚合

建议参数：

- `process_count`
- `thread_pool_size`

完成标准：

- 20+ sandbox 并发运行具备可控的资源占用和错误隔离

### Step 14: 增加工件与诊断体系 ✅ 已完成

目标：并发规模一上来，失败必须可追。

要做的事：

- 新增：
  - `android_world/runtime/artifacts.py`
- 每个 sandbox 独立输出目录
- 建议保存：
  - `logcat.txt`
  - `last_screenshot.png`
  - `page_source.xml`
  - `console.log`
  - `result.json`
- logcat 导出逻辑参考：
  - `examples/mobile-use/batch_dump_logcat.py`

完成标准：

- 任一 sandbox 失败后，都能快速拿到诊断材料

### Step 15: 改造 `run.py` 支持并发执行模式 ✅ 已完成

目标：把新的并发模型接到 AndroidWorld 正式入口。

要做的事：

- 修改：
  - `run.py`
- 新增：
  - `--execution_mode=serial`
  - `--execution_mode=parallel`
- 并发模式下：
  - 把 suite 切成 task instances
  - 分发到 sandbox pool / parallel runner
  - 聚合结果和 checkpoint

注意：

- `checkpoint` 逻辑要重新审视，避免并发写冲突

完成标准：

- `run.py` 能通过 AgentRuntime 并发跑多个 task instances

### Step 16: 明确 `android_server.py` 的服务化策略 ✅ 已完成（策略平平：不改动 server，并发 runner 为独立入口）

目标：避免把单例 server 和并发调度职责搅在一起。

建议做法：

- 首期 `android_server.py` 只支持“单 sandbox server”
- 并发控制交给 batch runner
- 如果未来需要服务化并发，新增 orchestrator 层，而不是污染现有 server

完成标准：

- 服务端策略清晰，不和 runner 路线互相打架

### Step 17: 补测试 ✅ 已完成

目标：避免后期接入回归。

要做的事：

- 单元测试：
  - `config_test.py`
  - `session_test.py`
  - `appium_connection_test.py`
  - `appium_adb_bridge_test.py`
  - `appium_controller_test.py`
  - `env_launcher_test.py` 的 `agentruntime` 分支
- 集成测试：
  - 单 sandbox smoke test
  - 小规模并发 smoke test
- 工具测试：
  - logcat 导出
  - artifacts 落盘

完成标准：

- 核心 bridge 和并发资源管理有测试覆盖

### Step 18: 分阶段验收

里程碑 A：单 sandbox 接入完成

验收标准：

- 能连接已有 sandbox
- 能创建 driver
- `get_state()` 正常
- click/input/back/home/swipe 正常
- 第一组白名单 task 跑通

里程碑 B：bridge 层完成

验收标准：

- `adb_utils` 常用调用可工作
- shell / push / pull / package / settings / dumpsys / broadcast 可工作
- 第二组、第三组白名单 task 至少部分跑通

里程碑 C：并发 runner 完成

验收标准：

- 多 sandbox 并发执行稳定
- token 不串线
- 工件输出完整
- 小规模批量评测可运行


## 13. 推荐实施顺序

如果只看实际推进顺序，建议严格按下面顺序执行：

1. `Step 0 ~ Step 2`
2. `Step 3 ~ Step 5`
3. `Step 6 ~ Step 9`
4. `Step 10`
5. `Step 11 ~ Step 14`
6. `Step 15 ~ Step 18`

原因：

- 先把单 sandbox 跑通，才能验证这条技术路线成立
- 再补 shell / 文件 / validator / 时间控制，避免并发后才暴露平台差异
- 最后做并发 runner，收益最高、返工最少


## 14. 工作量评估

### 14.1 单环境 Appium backend MVP

内容：

- 单 sandbox connect/create
- Appium driver 创建
- screenshot + page_source
- 基础动作
- 最小 controller 兼容

估算：

- 开发：`4 ~ 7` 人天
- 联调：`2 ~ 3` 人天
- 合计：`6 ~ 10` 人天

### 14.2 `execute_adb_call()` bridge 与 task 生命周期兼容

内容：

- `generic`
- `package_manager`
- `settings`
- `dumpsys`
- `send_broadcast`
- `push/pull`
- `get_current_activity`
- `get_orientation`
- task init / validator / shell / file / time 兼容

估算：

- 开发：`6 ~ 10` 人天
- 联调：`3 ~ 5` 人天
- 合计：`9 ~ 15` 人天

### 14.3 并发 runner 支持

内容：

- sandbox pool
- async 调度
- thread pool
- 多进程切片
- 工件输出
- 结果聚合

估算：

- 开发：`4 ~ 7` 人天
- 联调：`2 ~ 4` 人天
- 合计：`6 ~ 11` 人天

### 14.4 总体估算

如果目标是：

- AndroidWorld 正式支持 `E2B + Appium + android-sidecar + redroid` 接入
- 支持多 sandbox 并发执行
- 能跑单任务到小规模批量评测

更现实的整体工作量是：

- **`18 ~ 30` 人天**

如果只做“单 sandbox 接入 demo”，不做并发 runner，则是：

- **`6 ~ 10` 人天**


## 15. 关键风险与前置依赖

1. `mobile: shell` 权限与兼容性
   - 这是整个 bridge 的核心依赖。

2. `pull_file/push_file` 对关键路径的可达性
   - 尤其是数据库、媒体目录、app data。

3. 时间控制与 snapshot 恢复
   - 如果平台侧不支持，需要明确平台前置改造项。

4. `AppiumConnection.extra_headers` 串线
   - 这是并发设计里的硬风险。

5. `run.py` 的 checkpoint 并发语义
   - 需要重新设计或至少做串行化落盘。


## 16. 代码层 Review 补充（基于真实代码调研）

> 本节是在深入阅读项目源码后，对原方案的补充和修订。原方案框架方向正确，但有以下 6 个盲点需要在实施时特别注意。

### 16.1 盲点一：`execute_adb_call` 是 Protobuf oneof 路由，不是简单 shell 转发

**原方案描述不够精确的地方：**
原方案把 bridge 描述为"把 AdbRequest 映射成 Appium 调用"，但实际上 `adb_pb2.AdbRequest` 是一个 Protobuf message，其核心字段是 `oneof payload`，包含以下子类型：

- `GenericRequest` — 通用 shell 命令（最常用）
- `Tap` — 点击坐标
- `InputText` — 文字输入
- `StartActivity` — 启动 Activity
- `ForceStop` — 强制停止应用
- `GetCurrentActivity` — 获取前台 Activity
- `PackageManager` — pm 命令
- `PressButton` — 按键事件
- `Pull` — 从设备拉取文件（拉的是**目录**，不是单文件）
- `Push` — 推送文件到设备
- `SendBroadcast` — 发送 broadcast intent
- `GetOrientation` — 获取屏幕方向
- `SetOrientation` — 设置屏幕方向

**实施要求：**
`appium_adb_bridge.py` 必须用 `WhichOneof('payload')` 做 case dispatch，逐一处理每个 payload 类型，不能只处理 `generic` 就认为完工。

### 16.2 盲点二：`AppiumController` 必须继承 `base_wrapper.BaseWrapper`

**原方案描述不够精确的地方：**
原方案只说"对齐当前 controller 常用接口"，但实际上 `AndroidWorldController` 继承自 `android_env.wrappers.base_wrapper.BaseWrapper`。`AsyncAndroidEnv` 在调用 `controller.reset()` 和 `controller.step()` 时，会走 `BaseWrapper` 的包装逻辑，其中包括：

- `_process_timestep()` 钩子，用于往 observation 里注入 `forest` 和 `ui_elements`
- `execute_adb_call()` 的透传（BaseWrapper 默认透传到底层 env）

**实施要求：**
`AppiumController` 有两个选择：
1. **继承 `BaseWrapper`**，同时持有一个实现了 `execute_adb_call()` 的底层 `AppiumEnv`，这样 `AsyncAndroidEnv` 可以零修改复用。
2. **不继承 `BaseWrapper`**，直接实现 `AndroidWorldController` 的全部 property 和 method，并修改 `env_launcher.py` 中创建 `AsyncAndroidEnv` 的逻辑，接受 `AppiumController` 作为参数。

**推荐选择方案 1**，因为它改动最小，风险最低。

### 16.3 盲点三：A11y 树获取方式必须显式设定为 `UIAUTOMATOR`

**原方案描述不够精确的地方：**
原方案笼统提到"UI 树解析继续复用 `representation_utils.xml_dump_to_ui_elements`"，但没有说清楚获取 UI 树的上游链路。

实际代码中：
- `A11yMethod.A11Y_FORWARDER_APP` 依赖 gRPC wrapper (`a11y_grpc_wrapper.A11yGrpcWrapper`)，需要在设备上安装 a11y 转发 APK，并通过 gRPC 端口通信。**在 AgentRuntime 环境下完全不可用。**
- `A11yMethod.UIAUTOMATOR` 依赖 `adb_utils.uiautomator_dump(env)`，底层是 `adb shell uiautomator dump`，通过 `mobile: shell` 可以执行。**这是 AgentRuntime 下唯一可行的 A11y 方法。**

`get_controller()` 的当前逻辑是：
```python
if a11y_method is None:
    a11y_method = (
        A11yMethod.UIAUTOMATOR if device_mode == 'redroid'
        else A11yMethod.A11Y_FORWARDER_APP
    )
```

**实施要求：**
在 `env_launcher.py` 的 `AGENTRUNTIME` 分支中，必须强制传入 `a11y_method=A11yMethod.UIAUTOMATOR`，不能依赖默认值推导。

### 16.4 盲点四：`app_snapshot` 依赖 `/data/data/` 是阻塞性前置风险

**原方案描述不够精确的地方：**
原方案把 `app snapshot restore` 标记为"视首期范围决定"，但实际上 `task_eval.py` 的 `initialize_task()` **无条件调用** `app_snapshot.restore_snapshot()`：

```python
def _initialize_apps(self, env: interface.AsyncEnv) -> None:
    for app_name in self.app_names:
        if app_name and app_name != "clipper":
            try:
                app_snapshot.restore_snapshot(app_name, env.controller)
            except RuntimeError as error:
                logging.warning("Skipping app snapshot loading : %s", error)
```

`app_snapshot.restore_snapshot()` 的实现路径是：
1. 拼接路径：`/data/data/<package>/`（app 私有数据目录）
2. 调用 `file_utils.copy_data_to_device()` 把快照推送进去
3. 需要 root 权限或 `adb shell run-as <package>`

**这个路径在 Appium 环境下是否可达是最高优先级的验证项。**

**实施要求：**
在 Step 9（smoke test）之前，必须单独验证以下能力：
- `mobile: shell` 能否执行 `run-as <package>` 类命令
- Appium `push_file` 能否推文件到 `/data/data/<package>/` 路径
- 如果不可达，需要在 `AppiumEnv.push_file()` 里实现 workaround（如通过 `content://` provider 或 `am` 命令触发数据恢复）

如果 `/data/data/` 无法写入，则首期必须明确跳过所有依赖 snapshot restore 的任务，并在白名单里标注清楚。

### 16.5 盲点五：`pull_file` 实际拉取的是整个**目录**，不是单文件

**原方案描述不够精确的地方：**
`AndroidWorldController.pull_file()` 的实现是：

```python
def pull_file(self, remote_db_file_path, timeout_sec=None):
    remote_db_directory = os.path.dirname(remote_db_file_path)
    return file_utils.tmp_directory_from_device(
        remote_db_directory, self.env, timeout_sec
    )
```

它取的是 `remote_db_file_path` 的**父目录**，然后把整个目录 pull 下来，返回一个 context manager，退出时自动清理本地临时目录。

这意味着 bridge 里的 `Pull` 处理逻辑不能只实现"pull 单文件"，而要能 pull 一个目录（或至少把父目录的所有文件都 pull 下来放入同一个临时目录中）。

**实施要求：**
`appium_adb_bridge.py` 中处理 `Pull` 时：
- 输入是文件路径（如 `/data/data/org.tasks/databases/database.db`）
- 需要把其父目录（`/data/data/org.tasks/databases/`）的内容 pull 到本地临时目录
- Appium 的 `pull_file` 只支持单文件，需要先 `mobile: shell ls <dir>` 列目录，再循环 pull 每个文件

### 16.6 盲点六：`display_message` 走 broadcast，bridge 必须支持

**原方案描述不够精确的地方：**
`AsyncAndroidEnv.display_message()` 的实现是：

```python
def display_message(self, message: str, header: str = '') -> None:
    adb_utils.send_android_intent(
        command='broadcast',
        action='com.example.ACTION_UPDATE_OVERLAY',
        env=self.controller,
        extras={'task_type_string': header, 'goal_string': message},
    )
```

这在任务执行过程中（通过 `answer` action）会被触发，最终路径是 `adb_pb2.AdbRequest.SendBroadcast`。

**实施要求：**
`SendBroadcast` 的 bridge 实现是必须的，不是可选的，它影响任务执行时的 overlay 显示逻辑。即使 overlay 在 AgentRuntime 环境下不显示也无关紧要，broadcast 本身不能抛出异常。

---

## 17. 实施注意事项补充

基于代码 review，对原有各 Step 补充以下注意事项：

### Step 6 补充注意事项
- `AppiumController` **必须继承 `base_wrapper.BaseWrapper`**（见盲点二）
- 必须覆写 `_process_timestep()`，在 observation 中注入 `forest=None` 和 `ui_elements=<uiautomator解析结果>`
- `install_a11y_forwarding_app` 必须传 `False`

### Step 7 补充注意事项
- 必须用 `WhichOneof('payload')` 做 case dispatch（见盲点一）
- `Pull` 需要处理整目录 pull 语义（见盲点五）
- `SendBroadcast` 不可省略（见盲点六）
- `mobile: shell` 调用建议统一封装超时参数，默认值建议 30 秒（比 ADB 默认的 10 秒宽松）

### Step 8 补充注意事项
- `_get_env()` 的 agentruntime 分支必须强制传入 `a11y_method=A11yMethod.UIAUTOMATOR`（见盲点三）

### Step 9 补充注意事项（新增前置验证项）
在跑 smoke test 之前，必须先做以下平台能力验证（独立脚本，不跑完整 task）：
1. **`mobile: shell` 权限验证**：执行 `id`、`pm list packages`、`settings get global wifi_on`
2. **文件路径可达性验证**：`push_file` 到 `/storage/emulated/0/Download/test.txt`
3. **`/data/data/` 可达性验证**：尝试 `mobile: shell run-as <package> ls`（见盲点四）
4. **uiautomator dump 验证**：执行 `mobile: shell uiautomator dump /sdcard/ui.xml && cat /sdcard/ui.xml`
5. **broadcast 验证**：发送一条 broadcast，验证不抛异常即可

这 5 项验证决定白名单任务能否跑通，必须在 Step 9 的正式任务之前完成。


## 18. 最终结论

这次改造的正确方向，不是"让 AndroidWorld 想办法继续走 ADB"，而是：

- **把 AndroidWorld 改造成标准的 `E2B + Appium + android-sidecar + redroid` 接入模式**
- **在单 sandbox backend 上补齐 Appium bridge**
- **在多 sandbox runner 上补齐并发执行模型**

并且要明确：

- AndroidWorld 当前真实依赖面比"点点 UI"深得多
- shell、文件、validator、时间控制、snapshot 才是首期成败的关键
- 首期必须用任务白名单和能力矩阵控制范围，不能直接许诺"116 个任务全部支持"
- **`/data/data/` 可达性是里程碑 B 的真正拦路虎，必须在 Step 9 前提前验证**
- **AppiumController 必须继承 BaseWrapper，不能绕过**
- **A11y 方法必须强制使用 UIAUTOMATOR，不能依赖默认值**
