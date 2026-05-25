# AndroidWorld

<!-- mdlint off(WHITESPACE_LINE_LENGTH) -->

[![Unittests](https://github.com/google-research/android_world/actions/workflows/pytest.yml/badge.svg)](https://github.com/google-research/android_world/actions/workflows/pytest.yml)

<p align="center">
<a href="https://google-research.github.io/android_world/">官网</a> •
<a href="https://arxiv.org/pdf/2405.14573">论文</a> •
<a href="https://google-research.github.io/android_world/task_list.html">任务列表</a> •
<a href="https://docs.google.com/spreadsheets/d/1cchzP9dlTZ3WXQTfYNhh3avxoLipqHN75v1Tb86uhHo/edit?gid=0#gid=0">排行榜</a> •
<a href="README.md">English</a>
</p>

![Overview](assets/overview.png)

**AndroidWorld** 是一个用于构建和评测自主计算机控制智能体的环境。

它运行在 [腾讯云 AGR](https://cloud.tencent.com/product/agr) 云端 Android 沙箱上，
包含一个高度可复现的评测基准，涵盖 20 个应用的 116 个手工设计任务，通过随机参数动态
实例化，可产生数百万种独特的任务变体。

除内置任务外，还支持 MiniWoB++（[Liu et al.](http://arxiv.org/abs/1802.08802)）网页基准。

核心特性：

* **116 个多样化任务**，覆盖 20 个真实应用
* **动态任务实例化**，支持数百万种变体
* **持久化奖励信号**，评估结果可靠
* **云原生** — 运行在 AgentRuntime 沙箱，无需本地模拟器
* **并行评测** — 任务自动分配到多个沙箱
* **开放环境**，可接入数百万 Android 应用和网站
* **轻量级**（每沙箱 2 GB 内存，8 GB 磁盘）
* **可扩展设计**，方便添加新任务和新基准
* **集成 MiniWoB++ 网页任务**

---

## 安装

1. [可选] 推荐使用 conda：

    ```bash
    conda create -n android_world python=3.11.8
    conda activate android_world
    ```

2. 安装 AndroidWorld（需要 Python 3.11+）：

    ```bash
    git clone https://github.com/google-research/android_world.git
    cd ./android_world
    pip install -r requirements.txt
    python setup.py install
    ```

3. 安装 ffmpeg：

    ```bash
    # macOS
    brew install ffmpeg

    # Linux (Ubuntu/Debian)
    # sudo apt update && sudo apt install ffmpeg
    ```

---

## 配置

### 前置条件

- 腾讯云 AGR API 密钥（`E2B_API_KEY`）
- AGR 服务域名（如 `ap-guangzhou.tencentags.com`）
- 预构建的沙箱模板或预创建的沙箱 ID
- OpenAI 兼容的 LLM 接入点（OpenAI、Anthropic、Google 等）

### 第 1 步：配置环境

```bash
cp .env.example .env          # 填写 E2B_API_KEY, E2B_DOMAIN, OPENAI_API_KEY 等
cp sandboxes_example.yaml sandboxes.yaml  # 填入预创建的沙箱 ID
```

`.env` 文件支持所有配置项。你还可以创建 `.env.local` 文件用于本地覆写（不提交到 git）。

**优先级**：系统环境变量 > `.env.local` > `.env` > flag 默认值

### 第 2 步：配置 LLM 模型

在 `.env` 中设置模型接入点：

```bash
# OpenAI
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4o

# Anthropic Claude（通过 OpenAI 兼容端点）
OPENAI_API_KEY=sk-ant-xxxxx
OPENAI_BASE_URL=https://api.anthropic.com/v1
OPENAI_MODEL_NAME=claude-sonnet-4-20250514

# Google Gemini（通过 OpenAI 兼容端点）
OPENAI_API_KEY=your-gemini-key
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
OPENAI_MODEL_NAME=gemini-2.5-pro
```

`OPENAI_BASE_URL` 的处理逻辑：
- 以 `/v1` 结尾 → 自动追加 `/chat/completions`
- 已包含完整路径 → 直接使用
- 裸端点 → 追加 `/v1/chat/completions`

### 第 3 步：配置沙箱池

大规模并行评测时，预创建一批 AGR 沙箱并将 ID 写入 `sandboxes.yaml`：

```yaml
# sandboxes.yaml
sandbox_ids:
  - <sandbox-id-1>
  - <sandbox-id-2>
  - <sandbox-id-3>
```

传入 `--sandboxes_yaml=sandboxes.yaml`，Runner 会连接这些已有沙箱（不会创建/销毁），
自动分配任务。

关于如何预创建沙箱、管理沙箱生命周期，或在 AndroidWorld 基础上扩展自定义移动端自动化
工作流，请参考 AGS Cookbook 中的
[mobile-use 示例](https://github.com/TencentCloudAgentRuntime/ags-cookbook/tree/main/examples/mobile-use)。

---

## 快速开始

### 冒烟测试（串行模式）

```bash
python minimal_task_runner.py \
  --task=TasksDueOnDate \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

### 全量基准并行评测

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

提供 `--sandboxes_yaml` 后，执行模式自动切换为 `parallel`，沙箱数量从 YAML 中的
ID 列表自动推导。

### 运行部分任务

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml \
  --tasks=ContactsAddContact,SimpleSmsSend,MarkorCreateNote
```

---

## 执行模式

| 模式 | 触发方式 | 说明 |
|------|----------|------|
| **serial**（串行） | 默认，或 `--execution_mode=serial` | 单沙箱，任务按顺序执行 |
| **parallel**（并行） | 提供 `--sandboxes_yaml` 时自动激活 | 多沙箱，任务自动分配到各沙箱 |

并行模式下，Runner 会创建 `SandboxPool`，将任务实例分配到各沙箱，每个沙箱运行独立的
Agent 实例。

---

## 环境变量参考

| 变量名 | 是否必填 | 默认值 | 说明 |
|--------|----------|--------|------|
| `E2B_API_KEY` | 是 | — | AGR API 密钥 |
| `E2B_DOMAIN` | 是 | — | AGR 服务域名（如 `ap-guangzhou.tencentags.com`） |
| `SANDBOX_TEMPLATE` | 条件 | — | 创建沙箱时的模板名（使用 `sandboxes.yaml` 时不需要） |
| `SANDBOX_TIMEOUT` | 否 | `300` | 沙箱存活时间（秒） |
| `SANDBOX_COUNT` | 否 | `1` | 沙箱数量（使用 `sandboxes.yaml` 时自动推导） |
| `EXECUTION_MODE` | 否 | `serial` | `serial` 或 `parallel` |
| `THREAD_POOL_SIZE` | 否 | `4` | 每进程工作线程数 |
| `PROCESS_COUNT` | 否 | `1` | 进程数 |
| `OPENAI_API_KEY` | 是 | — | OpenAI 兼容 API 密钥 |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | 自定义 LLM 接入点 |
| `OPENAI_MODEL_NAME` | 否 | `gpt-4-turbo-2024-04-09` | 模型标识符 |

---

## 输出结构

评测完成后，结果保存在输出目录中：

```
output/runs/<run-id>/
├── summary.json        # 汇总统计（成功率、沙箱数等）
├── details.json        # 逐任务结果（任务名、是否成功、耗时等）
└── <sandbox-id>/       # 各沙箱的日志和截图
```

---

## 断点续跑

如果运行中途失败，可以使用 checkpoint 恢复：

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml \
  --checkpoint_dir=output/runs/<previous-run-id>
```

---

## 任务步数限制

自 2024/11/18 起，每个任务的 max_steps 已更新为人类平均完成时间的约 2 倍，确保 Agent
有足够时间完成任务，同时减少基准运行开销。
[逐任务更新详情](https://docs.google.com/spreadsheets/d/1KF-vY0Uy47o0mnursvs-HmS6hreU6U3rPrAjgEfjMK4/edit?usp=sharing)。

`n_task_combinations` 参数指定每个任务模板的参数组合数。例如对于短信任务，对应不同的
电话号码/消息组合。

---

## 运行 MiniWoB++ 任务

```bash
python run.py \
  --suite_family=miniwob \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

MiniWoB++ 的输入元素在 AndroidWorld 中被渲染为原生 Android UI 组件（如时间/日期选择器），
而非 HTML 控件。

<p align="center">
   <img src="assets/miniwob.png" style="width:30%">
</p>

---

## 创建自定义 Agent

1. 创建继承 `EnvironmentInteractingAgent` 的 Agent 类，实现 `step` 方法
2. 在 `run.py` 中导入并注册到 `_get_agent` 方法
3. 使用 `--agent_name=your_agent` 运行基准测试

可用 Agent：
- `t3a_gpt4` — Text-only 三阶段 Agent（推荐）
- `m3a_gpt4v` — 多模态三阶段 Agent（使用截图）
- `human_agent` — 人工操作模式

详见 [agents/README.md](android_world/agents/README.md)。

---

## 添加新任务

请参考 [任务添加指南](docs/tasks_guide.md)。

---

## 常见问题

**Q: 沙箱连接超时怎么办？**
检查 `E2B_DOMAIN` 是否正确，确认沙箱状态是否为 running。可通过 `e2b sandbox list` 查看。

**Q: 模型返回空响应？**
确认 `OPENAI_BASE_URL` 和 `OPENAI_MODEL_NAME` 配置正确。可用 curl 单独测试模型接入点：
```bash
curl -s $OPENAI_BASE_URL/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"your-model","messages":[{"role":"user","content":"hi"}]}'
```

**Q: 如何使用 Claude 模型？**
AndroidWorld 支持任何 OpenAI 兼容端点。Anthropic 提供了 OpenAI 兼容接口，配置如下：
```bash
OPENAI_API_KEY=sk-ant-xxxxx
OPENAI_BASE_URL=https://api.anthropic.com/v1
OPENAI_MODEL_NAME=claude-sonnet-4-20250514
```

---

## 引用

如果使用了我们的环境或数据，请引用：

```
@misc{rawles2024androidworlddynamicbenchmarkingenvironment,
      title={AndroidWorld: A Dynamic Benchmarking Environment for Autonomous Agents},
      author={Christopher Rawles and Sarah Clinckemaillie and Yifan Chang and Jonathan Waltz and Gabrielle Lau and Marybeth Fair and Alice Li and William Bishop and Wei Li and Folawiyo Campbell-Ajala and Daniel Toyama and Robert Berry and Divya Tyamagundlu and Timothy Lillicrap and Oriana Riva},
      year={2024},
      eprint={2405.14573},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2405.14573},
}
```

*This is not an officially supported Google product.*
