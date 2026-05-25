# AndroidWorld

<!-- mdlint off(WHITESPACE_LINE_LENGTH) -->

[![Unittests](https://github.com/google-research/android_world/actions/workflows/pytest.yml/badge.svg)](https://github.com/google-research/android_world/actions/workflows/pytest.yml)

<p align="center">
<a href="https://google-research.github.io/android_world/">Website</a> •
<a href="https://arxiv.org/pdf/2405.14573">Paper</a> •
<a href="https://google-research.github.io/android_world/task_list.html">Tasks</a> •
<a href="https://docs.google.com/spreadsheets/d/1cchzP9dlTZ3WXQTfYNhh3avxoLipqHN75v1Tb86uhHo/edit?gid=0#gid=0">Leaderboard</a> •
<a href="README_CN.md">中文文档</a>
</p>

![Overview](assets/overview.png)

**AndroidWorld** is an environment for building and benchmarking autonomous
computer control agents.

It runs on [TencentAGR](https://cloud.tencent.com/product/agr) cloud Android
sandboxes and contains a highly reproducible benchmark of 116 hand-crafted tasks
across 20 apps, which are dynamically instantiated with randomly-generated
parameters to create millions of unique task variations.

In addition to the built-in tasks, AndroidWorld also supports the popular web benchmark, MiniWoB++ from [Liu et al.](http://arxiv.org/abs/1802.08802).

Key features of AndroidWorld include:

* **116 diverse tasks** across 20 real-world apps
* **Dynamic task instantiation** for millions of unique variations
* **Durable reward signals** for reliable evaluation
* **Cloud-native** — runs on AgentRuntime sandboxes, no local emulator needed
* **Parallel evaluation** — distribute tasks across multiple sandboxes
* **Open environment** with access to millions of Android apps and websites
* **Lightweight footprint** (2 GB memory, 8 GB disk per sandbox)
* **Extensible design** to easily add new tasks and benchmarks
* **Integration with MiniWoB++** web-based tasks

See demo videos on our [website](https://google-research.github.io/android_world/).

## Installation

1. [Optional] It's recommended to use `conda`, which you can download [here](https://docs.anaconda.com/free/miniconda/miniconda-install/).

    ```bash
    conda create -n android_world python=3.11.8
    conda activate android_world
    ```

2. Install AndroidWorld. *Note: Python 3.11 or above is required.*

    ```bash
    git clone https://github.com/google-research/android_world.git
    cd ./android_world
    pip install -r requirements.txt
    python setup.py install
    ```

3. Install `ffmpeg`, if not already installed.

    ```bash
    # macOS
    brew install ffmpeg

    # Linux (Ubuntu/Debian)
    # sudo apt update && sudo apt install ffmpeg
    ```

## Configuration

### Prerequisites

- A TencentAGR API key (`E2B_API_KEY`)
- The AGR service domain for your region (e.g. `ap-guangzhou.tencentags.com`)
- A pre-built sandbox template or pre-created sandbox IDs
- An OpenAI-compatible LLM endpoint (OpenAI, Anthropic, Google, etc.)

### 1. Configure environment

```bash
cp .env.example .env          # fill in E2B_API_KEY, E2B_DOMAIN, OPENAI_API_KEY, …
cp sandboxes_example.yaml sandboxes.yaml  # fill in your pre-created sandbox IDs
```

The `.env` file supports all configuration. You can also create a `.env.local`
file for local overrides that should not be committed to git.

**Priority order:** OS environment > `.env.local` > `.env` > flag defaults

### 2. Configure the LLM model

Set the model endpoint in `.env` (or export in shell):

```bash
# OpenAI
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4o

# Anthropic Claude (via OpenAI-compatible endpoint)
OPENAI_API_KEY=sk-ant-xxxxx
OPENAI_BASE_URL=https://api.anthropic.com/v1
OPENAI_MODEL_NAME=claude-sonnet-4-20250514

# Google Gemini (via OpenAI-compatible endpoint)
OPENAI_API_KEY=your-gemini-key
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
OPENAI_MODEL_NAME=gemini-2.5-pro
```

The `OPENAI_BASE_URL` handling is smart: if it ends with `/v1`,
`/chat/completions` is appended automatically. If it already contains the full
path, it is used as-is.

### 3. Configure sandbox pool

For parallel evaluation, pre-create a pool of AGR sandboxes and list their IDs
in `sandboxes.yaml`:

```yaml
# sandboxes.yaml
sandbox_ids:
  - <sandbox-id-1>
  - <sandbox-id-2>
  - <sandbox-id-3>
```

Pass `--sandboxes_yaml=sandboxes.yaml` to either `minimal_task_runner.py` or
`run.py`. The runner connects to those sandboxes (instead of creating new ones)
and distributes tasks across them automatically.

For instructions on how to pre-create sandboxes, manage sandbox lifecycle, or
extend AndroidWorld with custom mobile automation workflows, refer to the
[mobile-use examples](https://github.com/TencentCloudAgentRuntime/ags-cookbook/tree/main/examples/mobile-use)
in the AGS Cookbook.

## Quickstart

### Smoke-test a single task (serial mode)

```bash
python minimal_task_runner.py \
  --task=TasksDueOnDate \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

### Run the full benchmark in parallel

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

When `--sandboxes_yaml` is provided, `execution_mode` is automatically set
to `parallel` and `sandbox_count` is derived from the number of sandbox IDs
listed in the file.

### Run a subset of tasks

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml \
  --tasks=ContactsAddContact,SimpleSmsSend,MarkorCreateNote
```

## Execution modes

| Mode | When | Description |
|------|------|-------------|
| **serial** | Default, or `--execution_mode=serial` | One sandbox, tasks run sequentially |
| **parallel** | Auto when `--sandboxes_yaml` is provided | Multiple sandboxes, tasks distributed across them |

In parallel mode, the runner creates a `SandboxPool`, distributes task instances
across sandboxes, and collects results. Each sandbox runs its own agent instance.

## Environment variables reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `E2B_API_KEY` | Yes | — | AGR API key |
| `E2B_DOMAIN` | Yes | — | AGR service domain (e.g. `ap-guangzhou.tencentags.com`) |
| `SANDBOX_TEMPLATE` | Conditional | — | Template name for creating new sandboxes (not needed if using `sandboxes.yaml`) |
| `SANDBOX_TIMEOUT` | No | `300` | Sandbox lifetime in seconds |
| `SANDBOX_COUNT` | No | `1` | Number of sandboxes (auto-derived when using `sandboxes.yaml`) |
| `EXECUTION_MODE` | No | `serial` | `serial` or `parallel` |
| `THREAD_POOL_SIZE` | No | `4` | Worker threads per process |
| `PROCESS_COUNT` | No | `1` | Number of processes |
| `OPENAI_API_KEY` | Yes | — | OpenAI-compatible API key |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Custom LLM endpoint |
| `OPENAI_MODEL_NAME` | No | `gpt-4-turbo-2024-04-09` | Model identifier |

## Output structure

After a run completes, results are saved to the output directory:

```
output/runs/<run-id>/
├── summary.json        # Aggregate stats (success rate, sandbox count, etc.)
├── details.json        # Per-task results (task_name, is_successful, duration, etc.)
└── <sandbox-id>/       # Per-sandbox logs and screenshots
```

## Resume from checkpoint

If a run fails part-way through, resume it:

```bash
python run.py \
  --suite_family=android_world \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml \
  --checkpoint_dir=output/runs/<previous-run-id>
```

## Task step limits

As of 11/18/2024, the max_steps/step_budget for each task have been updated to
approximately **2x the human average completion time**. This ensures agents have
sufficient time to complete tasks while reducing benchmark overhead.
[Per-task updates](https://docs.google.com/spreadsheets/d/1KF-vY0Uy47o0mnursvs-HmS6hreU6U3rPrAjgEfjMK4/edit?usp=sharing).

The `n_task_combinations` argument specifies how many parameter permutations to
use for each task. For example, for an SMS task, it would correspond to
different phone number/message combinations for each run.

## Running MiniWoB++ tasks

To run the MiniWoB++ web-based tasks in AndroidWorld, set
`--suite_family=miniwob`:

```bash
python run.py \
  --suite_family=miniwob \
  --agent_name=t3a_gpt4 \
  --device_mode=agentruntime \
  --sandboxes_yaml=sandboxes.yaml
```

A key advantage of running MiniWoB++ tasks is that common input elements are
rendered as native, commonly used Android UI widgets, rather than as HTML. Thus
agents must learn to use universal widgets such as time- and date-pickers:

<p align="center">
   <img src="assets/miniwob.png" style="width:30%">
</p>

## Create your own agent

In addition to the agents we provide [here](https://github.com/google-research/android_world/tree/main/android_world/agents), you can also easily create your own agent and run the benchmark with it as follows.

1. Create an agent class that inherits from [EnvironmentInteractingAgent](https://github.com/google-research/android_world/blob/6e4feb00702735c9a7485f4ae714528a058cb2b7/android_world/agents/base_agent.py#L39C1-L39C44) and implement the [step](https://github.com/google-research/android_world/blob/6e4feb00702735c9a7485f4ae714528a058cb2b7/android_world/agents/base_agent.py#L116) method.
In the current workflow, the agent tries to complete a task in a for loop. In each round, the [step](https://github.com/google-research/android_world/blob/6e4feb00702735c9a7485f4ae714528a058cb2b7/android_world/agents/base_agent.py#L116) method will be called and this is where you implement your agent's logic. A typical approach involves first gathering information like the current screenshot, the UI elements (like buttons, icons) through the AndroidEnv instance within the agent, selecting one of the [supported actions](https://github.com/google-research/android_world/blob/main/android_world/env/json_action.py), executing it through the AndroidEnv and returning an [AgentInteractionResult](https://github.com/google-research/android_world/blob/6e4feb00702735c9a7485f4ae714528a058cb2b7/android_world/agents/base_agent.py#L26). The `done` property on AgentInteractionResult should be set to true to indicate that the task is finished.

2. Import your agent in [run.py](https://github.com/google-research/android_world/blob/main/run.py) and also add it into the [_get_agent](https://github.com/google-research/android_world/blob/15471441ac306ff08bca87454b1b546ae81db7af/run.py#L147) method which takes in your agent's name and return an instance of it.

3. Now you can run the benchmark with your new agent using the command above with the `agent_name` flag changed to your agent's name.

## Adding new tasks

Please see [the guide](https://github.com/google-research/android_world/blob/main/docs/tasks_guide.md) on adding new tasks to AndroidWorld.

## Citation

If you use our environment or data, please cite our paper:

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
