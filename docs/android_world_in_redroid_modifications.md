# mellowxu 修改说明文档

> 修改者：mellowxu  
> 修改日期：2026-05-13 ~ 2026-05-15  
> 目标：使 AndroidWorld benchmark 支持 redroid（容器化 Android）+ uiautomator 运行，同时保持评测语义和难度不变

---

## 一、背景与动机

AndroidWorld 原始设计基于 Google Emulator + snapshot 机制运行。在 redroid 容器化 Android 环境下，存在以下根本差异：

| 维度 | Emulator | Redroid |
|------|----------|---------|
| 控制接口 | gRPC + emulator console | ADB 直连 |
| 状态恢复 | 预制 snapshot | 无 snapshot，需手动复位 |
| Agent 操作 | 鼠标/触摸/键盘全支持 | uiautomator 仅支持 click |
| 包名体系 | GMS（Google 签名） | AOSP（开源签名） |
| 默认 locale | en | zh-CN |
| Chrome | 预装 | 需手动安装 |
| SMS/Clipboard | emulator console | content provider / adb am |

所有修改均围绕上述差异展开，**不改变任务目标、评估标准或 agent 交互步骤数**。

---

## 二、修改文件总览

共修改 **12 个已有文件** + **1 个新增文件**，按职责分为三类：

### A. 评测逻辑文件（5 个）

| # | 文件 | 修改类型 | 语义影响 |
|---|------|----------|----------|
| 1 | `task_evals/single/browser.py` | 交互适配 | drag→click，步骤数不变 |
| 2 | `task_evals/single/clock.py` | 职责拆分 | 无 |
| 3 | `task_evals/common_validators/sms_validators.py` | 路径/方式适配 | 无 |
| 4 | `task_evals/single/audio_recorder.py` | pre-warm | 无 |
| 5 | `task_evals/single/vlc.py` | pre-warm | 无 |

### B. 基础设施文件（6 个）

| # | 文件 | 修改类型 | 语义影响 |
|---|------|----------|----------|
| 6 | `env/adb_utils.py` | 多项平台适配 | 无 |
| 7 | `env/setup_device/apps.py` | 安装流程适配 | 无 |
| 8 | `env/setup_device/setup.py` | 初始化流程适配 | 无 |
| 9 | `agents/infer.py` | LLM 端点适配 | 无 |
| 10 | `utils/contacts_utils.py` | locale 适配 | 无 |
| 11 | `env/device_constants.py` | 路径大小写修正 | 无 |

### C. 新增文件（1 个）

| # | 文件 | 功能 | 语义影响 |
|---|------|------|----------|
| 12 | `env/redroid_env.py` | redroid 设备后端 | 无 |

### D. 框架入口文件（1 个）

| # | 文件 | 修改类型 | 语义影响 |
|---|------|----------|----------|
| 13 | `env/env_launcher.py` | 增加设备模式选择 | 无 |

---

## 三、各文件详细修改说明

---

### 3.1 `task_evals/single/browser.py` — BrowserDraw 交互适配

**修改日期**：2026-05-13, 2026-05-15

**修改原因**：

原始 BrowserDraw 的 canvas 绘图依赖鼠标拖拽（mousedown → mousemove → mouseup），但 agent 通过 Accessibility Service（uiautomator）操作界面，该服务只能发出 click 事件，无法产生 drag 手势，导致任务在 redroid + Chrome 148 下必然失败。

**设计原则**：

保留"选色→绘制"两步交互语义，不降低测试难度。agent 必须先从色板选择颜色，再点击 canvas 绘制，最后提交。与原始拖拽绘制的区别仅在于手势从 drag 简化为 click（uiautomator 限制），交互步骤数不变。

**具体修改（共 8 处）**：

#### 修改 1：canvas 标签增加 accessibility 属性

```
添加 tabindex="0" role="button"
aria-label="Drawing canvas, click to draw with selected color"
```

**目的**：让 uiautomator 将 canvas 识别为可点击的 Button 节点，使 agent 能够在 accessibility tree 中找到并点击 canvas；aria-label 提示"点击以用选中颜色绘制"，保持两步交互语义。

#### 修改 2：displayTaskColors 目标色块增加文字内容和 aria-label

```
为目标颜色的 <div> 写入 textContent = color（如 "#800000"）
并设 color: transparent 令文字不可见但可被 accessibility 读取；
同时设 aria-label="target color #xxxxxx"、title=color。
```

**目的**：uiautomator 无法读取 CSS background-color，原始代码中 agent 完全看不到目标颜色，直接报 infeasible。修改后 accessibility tree 中出现 `text="target color #800000"` 等节点，agent 可正确读取。

#### 修改 3：createColorPalette 颜色按钮增加 aria-label + 选中颜色状态提示

```
为每个色板按钮加 aria-label="color #xxxxxx"、title=color，使 agent 能识别颜色。
在颜色按钮的 click 回调中同步设置 ctx.strokeStyle 和 ctx.fillStyle，
但不自动绘制，仅更新 "Selected color" 状态提示文本。
```

**目的**：
1. aria-label 使 agent 可识别颜色；
2. 原始代码只设置 strokeStyle，fillStyle 未同步导致圆点颜色错误（始终为黑色），这是原始代码 bug 的修复；
3. **保留"选色→绘制"两步交互语义**，agent 必须先选色再点击 canvas 绘制，不可省略绘制步骤。

> **重要**：早期版本曾将选色即绘制（select-color-auto-draw），将两步交互合并为一步，降低了测试难度。已修正为仅更新状态文本，不自动绘制。

#### 修改 4：drawDot 新增统一绘点函数

```
先用 fillRect 画一个 12×12 的实心矩形（关闭 imageSmoothingEnabled），
再用 arc 画可见圆点。
```

**目的**：evaluateTask 通过像素级 hex 比较判断是否使用了目标颜色。canvas 2D arc 绘制时边缘存在抗锯齿，边缘像素颜色不精确，导致即使颜色正确也匹配失败。fillRect 可保证中心区域像素颜色完全等于指定的 hex 值，从而通过评估。

**关键约束**：此函数仅在 `handleCanvasClick`（accessibility click 路径）中调用，**不在 `startDrawing` 中调用**，因此 drag 模式下不会产生额外的 fillRect 方块，与原始行为完全一致。

> **重要**：早期版本曾在 startDrawing 中调用 drawDot()，导致 drag 模式下 mousedown 起点处出现额外的 12×12 小方块。已移除。

#### 修改 5：新增 canvas click 事件监听

```
canvas.addEventListener('click', handleCanvasClick)
```

**目的**：uiautomator 只能发出 click 事件，无法触发 mousedown/mousemove，必须通过 click 事件处理 accessibility service 的绘制请求。

#### 修改 6：handleCanvasClick / startDrawing 修复坐标兼容性

```
handleCanvasClick：当 accessibility service 发出的 click 事件
clientX/Y 为 0 时，fallback 到 canvas 中心坐标，并加随机偏移
防止多次点击重叠。

startDrawing：改用显式 undefined 判断代替 ||，避免 clientX=0
时错误地读取 touches[0]（原代码 `e.clientX || e.touches[0].clientX`
在 clientX=0 时会误取 touch 事件坐标）。

startDrawing 不调用 drawDot()：原始代码中 startDrawing 仅记录
坐标不绘制，draw 由后续 mousemove 触发。保持一致。
```

#### 修改 7：handleCanvasClick 增加 mousedown 防重入

```
记录 mouseDownTime，handleCanvasClick 在 mousedown 后 100ms 内
跳过执行，避免真实鼠标拖拽后 click 事件导致双重绘制。
Accessibility click 无前置 mousedown，不受影响。
```

#### 修改 8：HTML 新增选中颜色状态提示

```html
<p id="selectedColorInfo">No color selected</p>
```

选中颜色后更新为 "Selected color: #xxxxxx"，为 agent 提供状态反馈。

**修改语义等价性总结**：

1. **评估逻辑完全不变**：evaluateTask() 未修改，仍检查 canvas 像素中是否包含 3 种目标颜色。
2. **交互步骤数不变**：原始 drag 模式需"选色→拖拽绘制"，click 模式需"选色→点击绘制"，步骤数一致。agent 必须理解色板和 canvas 是两个不同交互目标，不可省略绘制步骤。
3. **drag 模式行为完全不变**：startDrawing 仅记录坐标（与原始一致），draw 通过 stroke() 画线（与原始一致），drawDot 仅在 click 路径调用，drag 模式下不会产生额外的 fillRect 方块。
4. **drag→click 的简化是 uiautomator 硬件限制所致，不可规避**。每项修改都有明确的 redroid/uiautomator 适配必要性，无冗余改动。

---

### 3.2 `task_evals/single/clock.py` — 拆分 _close_clock_app 职责

**修改日期**：2026-05-13（初版）/ 2026-05-14（完整修复）

**修改内容**：

1. `_close_clock_app`：改为 `press_home_button`，仅退出 UI，不清除数据。避免原 pm clear 在 tear_down 时杀死 Timer Service 导致状态丢失。
2. 新增 `_reset_stopwatch`：在秒表任务的 `initialize_task` 中单独调用 pm clear，将秒表复位到 "00:00:00 paused" 初态。

**原始设计意图**：

上游代码用 pm clear 同时实现两件事：
1. 关闭时钟 UI
2. 程序化复位秒表状态（秒表无 ADB 接口，只能靠清数据复位）

这在 Google 官方模拟器 + snapshot 机制下工作正常，因为 `restore_snapshot` 会在 pm clear 之前先恢复干净快照，pm clear 的数据清除有快照兜底。

**在 redroid 上的问题**：

redroid 容器无预制快照（`/data/data/android_world/snapshots/` 不存在），`restore_snapshot` 抛 RuntimeError 被 warning 忽略。此时 pm clear 的时序：
- `initialize_task`: pm clear → 秒表复位 ✓（对秒表任务是正确的）
- `tear_down`: pm clear → 清除 agent 任务执行结果（无害，`is_successful` 在 tear_down 之前调用）

实际上原实现对 ClockTimerEntry 的 `is_successful` 评估无影响；但 `_close_clock_app` 同时被 `initialize_task` 和 `tear_down` 调用，连续运行多个任务时逻辑上等价于原始设计，结果是正确的。

**为何仍需拆分**：

ClockTimerEntry 的 `initialize_task` 不需要清数据，pm clear 是多余操作；若将来秒表任务和 Timer 任务混合连续运行，保持职责分离更安全可靠。

**对评测语义/难度的影响**：无。

---

### 3.3 `task_evals/common_validators/sms_validators.py` — SMS 数据库路径和清除方式适配

**修改日期**：2026-05-14

**修改内容**：

1. **mmssms.db 物理路径**：按 device_mode 分别走不同路径。
   - Emulator: `/data/data/com.android.providers.telephony/databases/mmssms.db`
   - Redroid: `/data/user_de/0/com.android.providers.telephony/databases/mmssms.db`
   
   原因：Android 10+ 的 TelephonyProvider 使用 device-protected storage，redroid 精简容器中 `/data/data/` 路径不存在。

2. **clear_sms_and_threads**：redroid 下改用 ContentProvider delete 替代直接 SQL DELETE。
   
   原因：redroid 的 sqlite3 执行 DELETE ON sms 表时触发 FTS 虚拟表的 `sms_words_delete` trigger，缺少 `PRAGMA trusted_schema=ON` 会静默失败。ContentProvider delete 走 TelephonyProvider 内部逻辑，可正确处理 FTS。

**对评测语义/难度的影响**：无。两种方式都是清除 SMS 数据，功能等价。`is_successful` 检查逻辑（check_sms_sent）未修改，仍读取 mmssms.db 的 sms 表判断发送结果。修改仅影响"数据清理"环节。

---

### 3.4 `task_evals/single/audio_recorder.py` — 录音目录 pre-warm

**修改日期**：2026-05-14

**修改内容**：

新增 `_prewarm_audio_recorder()`，在 `initialize_task` 中调用。检测 AudioRecorder 的录音目录是否存在，若不存在则用 monkey 启动 AudioRecorder 一次（触发目录创建），再 force-stop。

**修改原因**：

AudioRecorder 每次 setup 执行 pm clear，清空录音目录。若该目录在 `initialize_task` 时不存在，`get_file_list_with_metadata` 会抛 RuntimeError 导致 INIT_FAIL。此问题在 redroid 上必然发生（无快照恢复），在 Emulator 上也可能发生（如果快照中该目录不存在）。

**对评测语义/难度的影响**：无。pre-warm 仅确保录音目录存在，不改变 app 的功能行为。agent 的任务目标和评估标准完全不变。pre-warm 在 agent 开始操作之前完成。

---

### 3.5 `task_evals/single/vlc.py` — VLC 数据库 pre-warm

**修改日期**：2026-05-14

**修改内容**：

新增 `_prewarm_vlc()`，在 `_VLC.initialize_task` 中调用。检测 VLC 的 `vlc_media.db` 是否存在，若不存在则用 monkey 启动 VLC 一次（触发 db 创建），再 force-stop。

**修改原因**：

VLC 每次 setup 会执行 pm clear，清空 app_db 目录。若在全集跑时两个 VLC task 之间有其他 task 触发了 pm clear（或容器重启），`vlc_media.db` 会消失，导致 INIT_FAIL。此问题在 redroid 上必然发生（无快照恢复），在 Emulator 上也可能发生。

**对评测语义/难度的影响**：无。pre-warm 仅确保 VLC 数据库存在，不改变 app 的功能行为。agent 的任务目标和评估标准完全不变。pre-warm 在 agent 开始操作之前完成。

---

### 3.6 `env/adb_utils.py` — ADB 工具函数平台适配

**修改日期**：2026-05-13, 2026-05-14

**修改内容**（跨 4421109 和 39f406b 两个提交）：

1. **launch_app**：增加 AOSP 包名 fallback（如 clock → `com.android.deskclock`），Emulator 上用 GMS 包名，redroid 上可能只有 AOSP 包名。
2. **call_emulator**：redroid 模式下跳过 emulator gRPC 调用（无 emulator）。
3. **send_sms / insert_sms**：redroid 下改用 content provider 方式发送/插入 SMS（emulator 用 emulator console）。
4. **get_clipboard_contents**：指定 clipper broadcast 的显式 component name，Android 10+ 隐式 broadcast 不再投递到后台 receiver。
5. **set_clipboard_contents**：改用 `am start`（非 broadcast）触发 clipper Main Activity，Android 10+ 后台进程 `setPrimaryClip()` 被静默忽略。
6. **get_all_package_names**：新增函数，用于探测已安装包。

**对评测语义/难度的影响**：无。所有修改均为基础设施层的平台适配，不改变任务目标、评估标准或 agent 交互流程。功能行为与 Emulator 路径等价。

---

### 3.7 `env/setup_device/apps.py` — 应用安装和设置适配

**修改日期**：2026-05-14

**修改内容**（跨 39f406b 提交和未提交变更）：

1. **download_app_data**：增加 `local_apk_dir` 参数，优先从本地目录查找 APK，避免 redroid 环境下无 GCS 访问权限时下载失败。
2. **ChromeApp**：增加 `apk_names` 属性，指定 Chrome 148 APK 文件名，redroid 镜像不预装 Chrome，需显式安装。
3. **ClipperApp.setup**：简化为 grant READ_CLIPBOARD appop + launch + close，移除原来点击 "Continue"/"OK" 按钮的 UI 交互流程（redroid 上 Clipper 首次启动的同意对话框可能不存在或 UI 不同）。

**对评测语义/难度的影响**：无。所有修改均为安装/设置流程的平台适配。ClipperApp.setup 简化不影响评测逻辑，Clipper 仅用于 get/set clipboard 工具函数。

---

### 3.8 `env/setup_device/setup.py` — 设备初始化流程适配

**修改日期**：2026-05-14

**修改内容**：

1. 增加 `local_apk_dir` 支持，redroid 下优先从本地 APK 目录安装。
2. 增加 APK 安装状态检测（`pm list packages`），避免重复安装。
3. redroid 模式下跳过 emulator-specific 的 setup 步骤（如 gRPC 调用）。
4. 支持 AOSP 和 GMS 两种包名体系。

**对评测语义/难度的影响**：无。所有修改均为设备初始化流程的平台适配，不改变任务目标、评估标准或 agent 交互流程。

---

### 3.9 `agents/infer.py` — LLM 推理端点适配

**修改日期**：2026-05-14

**修改内容**（跨 8d13859 和 39f406b 两个提交）：

1. **Gpt4Wrapper**：支持 `OPENAI_BASE_URL` 环境变量，允许连接 OpenAI 兼容端点（如 Tencent LKEAP、Azure OpenAI）。自动判断 URL 是否已包含 `/chat/completions` 后缀，避免重复拼接。
2. **Gpt4Wrapper**：启发式判断模型是否需要 `max_completion_tokens` 替代 `max_tokens`（gpt-5/o1/o3/o4 系列），并自动移除不支持的 `temperature` 参数。
3. **Gpt4Wrapper**：增加 API 错误自动恢复逻辑，当端点返回参数格式错误时自动重试（如 max_tokens → max_completion_tokens、移除 temperature）。

**对评测语义/难度的影响**：无。所有修改均为 LLM 调用层的模型/端点适配，不改变任务目标、评估标准或 agent 交互流程。使用相同模型时，agent 的推理能力不变。

---

### 3.10 `utils/contacts_utils.py` — 联系人保存按钮 locale 适配

**修改日期**：2026-05-14

**修改内容**：

`add_contact` 中 `find_and_click_element("SAVE")` 改为先尝试 "SAVE"（英文 locale），失败后尝试 "保存"（中文 locale），都失败则抛异常。

**修改原因**：

AOSP Contacts 应用了系统 locale。Emulator 默认 locale=en，按钮文本为 "SAVE"；redroid 容器默认 locale=zh-CN，按钮文本为 "保存"。原始代码硬编码 "SAVE"，在中文 locale 下必然失败。

**对评测语义/难度的影响**：无。仅适配 UI 文本的 locale 差异，功能行为等价（都是点击保存按钮）。

---

### 3.11 `env/device_constants.py` — Markor 数据目录大小写修正

**修改日期**：2026-05-14

**修改内容**：

`MARKOR_DATA` 从 `/storage/emulated/0/Documents/Markor`（大写 M）改为 `/storage/emulated/0/Documents/markor`（小写 m）。

**修改原因**：

Markor v0.146 在 AOSP/redroid 上创建的数据目录为小写 `markor`，而上游 AndroidWorld 硬编码了大写 `Markor`（Google 签名版的行为）。redroid 上用大写路径会找不到目录，导致所有 Markor 任务误判为失败。

**对评测语义/难度的影响**：无。仅修正文件路径使其匹配实际文件系统状态。若路径错误则任务必然失败，修正后才能正确评估。

---

### 3.12 `env/redroid_env.py` — 新增 redroid 设备后端

**修改日期**：2026-05-14

**功能说明**：

实现 AndroidEnvInterface 的 redroid 适配层，通过 adb 直连 redroid 容器。提供 `reset()`、`step()`、`get_state()` 等接口，使 AndroidWorld 的 agent/task 框架能在 redroid 设备上运行。

**核心特性**：
- 通过 adb controller 执行所有设备操作
- 自动检测 adb 连接断开并重连（`_looks_like_offline` + `_build_resilient_exec`）
- 使用 uiautomator dump 获取 UI 层级
- 使用 adb screenshot 获取屏幕截图

**对评测语义/难度的影响**：无。纯基础设施层的新增代码，提供与 Emulator 后端等价的接口。

---

### 3.13 `env/env_launcher.py` — 增加设备模式选择

**修改日期**：2026-05-14

**修改内容**：

1. `DeviceMode` 枚举增加 `REDROID` 选项。
2. `load_and_setup_env` 根据 device_mode 选择 Emulator 或 Redroid 后端。
3. Redroid 模式下使用 adb 直连，无需 gRPC/emulator console。

**对评测语义/难度的影响**：无。纯基础设施层的设备后端选择，不改变任务目标、评估标准或 agent 交互流程。

---

## 四、修改分类总结

### 4.1 按修改动机分类

| 动机 | 涉及文件 | 说明 |
|------|----------|------|
| uiautomator 只支持 click | browser.py | canvas drag→click 适配 |
| uiautomator 无法读取 CSS/background | browser.py | aria-label + textContent 替代 |
| canvas arc 抗锯齿导致 hex 不匹配 | browser.py | fillRect 保证精确颜色 |
| 无 snapshot 机制 | clock.py, audio_recorder.py, vlc.py | pre-warm / 职责拆分 |
| AOSP vs GMS 包名差异 | adb_utils.py, apps.py, setup.py, device_constants.py | fallback 路径 / 大小写修正 |
| Android 10+ 权限/广播限制 | adb_utils.py, sms_validators.py | 显式 component / content provider |
| locale 差异 | contacts_utils.py | SAVE→SAVE/保存 fallback |
| 无 Chrome 预装 | apps.py | Chrome APK 安装 |
| 无 GCS 访问 | apps.py, setup.py | 本地 APK 目录 |
| LLM 端点兼容 | infer.py | OpenAI 兼容 API 适配 |
| redroid 设备后端 | redroid_env.py, env_launcher.py | 新增后端 + 模式选择 |

### 4.2 对评测语义/难度的影响评估

| 影响级别 | 文件数 | 说明 |
|----------|--------|------|
| **无影响** | 12 | 基础设施/路径/locale 适配，功能等价 |
| **交互方式变化但步骤数不变** | 1 | browser.py：drag→click，两步交互语义保留 |
| **降低难度** | 0 | 无 |

**唯一涉及交互方式变化的文件是 browser.py**，其修改已被严格审查，确保：
- 评估逻辑（evaluateTask）完全不变
- 交互步骤数不变（选色→绘制，两步）
- drag 模式行为完全不变
- 无 auto-draw（选色不自动绘制）
- 无多余 fillRect（drawDot 不在 startDrawing 中调用）

---

## 五、已修复的早期问题

在修改迭代过程中，曾出现以下降低评测语义的问题，**已全部修正**：

| 问题 | 文件 | 描述 | 修正措施 |
|------|------|------|----------|
| 选色即绘制 | browser.py | createColorPalette 中点击颜色按钮自动在 canvas 绘制，将两步交互合并为一步 | 移除 auto-draw，仅更新 selectedColorInfo 状态文本 |
| drawDot 在 startDrawing 中调用 | browser.py | drag 模式下 mousedown 起点处产生额外 12×12 fillRect 方块 | 从 startDrawing 中移除 drawDot() 调用，仅在 handleCanvasClick 中调用 |
| canvas inline onclick 双触发 | browser.py | canvas 标签上同时有 onclick 和 addEventListener('click')，导致双重绘制 | 移除 inline onclick，仅保留 addEventListener |

---

## 六、提交历史

| 提交哈希 | 日期 | 主要内容 |
|----------|------|----------|
| 4421109 | 2026-05-13 | adb_utils AOSP fallback, browser.py 初版 |
| 39f406b | 2026-05-14 | redroid_env, env_launcher, sms_validators, apps, setup, infer, contacts, clock, vlc, audio_recorder, device_constants |
| 8d13859 | 2026-05-14 | infer.py OpenAI 兼容端点 |
| (未提交) | 2026-05-15 | browser.py 修正：移除 auto-draw、移除 startDrawing 中的 drawDot、添加修改注释 |
