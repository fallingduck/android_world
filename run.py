# Copyright 2026 The android_world Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Run eval suite.

The run.py module is used to run a suite of tasks, with configurable task
combinations, environment setups, and agent configurations. You can run specific
tasks or all tasks in the suite and customize various settings using the
command-line flags.
"""

from collections.abc import Sequence
import os

from absl import app
from absl import flags
from absl import logging
from android_world import checkpointer as checkpointer_lib
from android_world import registry
from android_world import suite_utils
from android_world.agents import base_agent
from android_world.agents import human_agent
from android_world.agents import infer
from android_world.agents import m3a
from android_world.agents import random_agent
from android_world.agents import seeact
from android_world.agents import t3a
from android_world.env import android_world_controller
from android_world.env import env_launcher
from android_world.env import interface
from android_world.env.agentruntime import config as agentruntime_config_lib

logging.set_verbosity(logging.WARNING)

os.environ['GRPC_VERBOSITY'] = 'ERROR'  # Only show errors
os.environ['GRPC_TRACE'] = 'none'  # Disable tracing


def _load_env_file(filename: str) -> None:
  """Load KEY=VALUE lines from a file in the project root (if present).

  Variables already set in the environment are NOT overridden, so OS-level
  env vars always take precedence over .env files.

  Args:
    filename: Bare filename (e.g. '.env' or '.env.local') relative to the
      directory that contains run.py.
  """
  path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
  if not os.path.isfile(path):
    return
  try:
    with open(path, 'r', encoding='utf-8') as f:
      for raw in f:
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
          continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
          os.environ[key] = value
  except OSError:
    pass


def _load_local_env() -> None:
  """Load .env (mobile-use standard) then .env.local (local overrides).

  Priority (high → low):
    1. OS environment variables (never overridden)
    2. .env.local (local developer overrides, not committed)
    3. .env (project-level defaults, matches mobile-use convention)
  """
  # Load base .env first so .env.local can override it.
  _load_env_file('.env')
  _load_env_file('.env.local')


_load_local_env()

# Register AgentRuntime flags AFTER env is loaded so flag defaults that read
# from os.environ (e.g. E2B_DOMAIN, SANDBOX_TEMPLATE) see the env-file values.
agentruntime_config_lib.register_flags()


def _find_adb_directory() -> str:
  """Returns the directory where adb is located."""
  potential_paths = [
      os.path.expanduser('~/Library/Android/sdk/platform-tools/adb'),
      os.path.expanduser('~/Android/Sdk/platform-tools/adb'),
  ]
  for path in potential_paths:
    if os.path.isfile(path):
      return path
  raise EnvironmentError(
      'adb not found in the common Android SDK paths. Please install Android'
      " SDK and ensure adb is in one of the expected directories. If it's"
      ' already installed, point to the installed location.'
  )


_ADB_PATH = flags.DEFINE_string(
    'adb_path',
    _find_adb_directory(),
    'Path to adb. Set if not installed through SDK.',
)
_EMULATOR_SETUP = flags.DEFINE_boolean(
    'perform_emulator_setup',
    False,
    'Whether to perform emulator setup. This must be done once and only once'
    ' before running Android World. After an emulator is setup, this flag'
    ' should always be False.',
)
_DEVICE_CONSOLE_PORT = flags.DEFINE_integer(
    'console_port',
    5554,
    'The console port of the running Android device. This can usually be'
    ' retrieved by looking at the output of `adb devices`. In general, the'
    ' first connected device is port 5554, the second is 5556, and'
    ' so on.',
)
_DEVICE_MODE = flags.DEFINE_enum(
    'device_mode',
    env_launcher.DeviceMode.EMULATOR.value,
    [mode.value for mode in env_launcher.DeviceMode],
    'Which device backend to use.',
)
_ADB_DEVICE_NAME = flags.DEFINE_string(
    'adb_device_name',
    '',
    'Explicit adb serial/endpoint. Required for redroid mode.',
)
_GRPC_PORT = flags.DEFINE_integer(
    'grpc_port',
    8554,
    'The port for gRPC communication with the emulator backend.',
)
_A11Y_METHOD = flags.DEFINE_string(
    'a11y_method',
    '',
    'Optional accessibility retrieval method override. Empty means "do not'
    ' override" (the controller picks a sensible default per device_mode).'
    ' Valid non-empty values: '
    + '|'.join(m.value for m in android_world_controller.A11yMethod)
    + '.',
)

_SUITE_FAMILY = flags.DEFINE_enum(
    'suite_family',
    registry.TaskRegistry.ANDROID_WORLD_FAMILY,
    [
        # Families from the paper.
        registry.TaskRegistry.ANDROID_WORLD_FAMILY,
        registry.TaskRegistry.MINIWOB_FAMILY_SUBSET,
        # Other families for more testing.
        registry.TaskRegistry.MINIWOB_FAMILY,
        registry.TaskRegistry.ANDROID_FAMILY,
        registry.TaskRegistry.INFORMATION_RETRIEVAL_FAMILY,
    ],
    'Suite family to run. See registry.py for more information.',
)
_TASK_RANDOM_SEED = flags.DEFINE_integer(
    'task_random_seed', 30, 'Random seed for task randomness.'
)

_TASKS = flags.DEFINE_list(
    'tasks',
    None,
    'List of specific tasks to run in the given suite family. If None, run all'
    ' tasks in the suite family.',
)
_N_TASK_COMBINATIONS = flags.DEFINE_integer(
    'n_task_combinations',
    1,
    'Number of task instances to run for each task template.',
)

_CHECKPOINT_DIR = flags.DEFINE_string(
    'checkpoint_dir',
    '',
    'The directory to save checkpoints and resume evaluation from. If the'
    ' directory contains existing checkpoint files, evaluation will resume from'
    ' the latest checkpoint. If the directory is empty or does not exist, a new'
    ' directory will be created.',
)
_OUTPUT_PATH = flags.DEFINE_string(
    'output_path',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', 'runs'),
    'The path to save results to if not resuming from a checkpoint is not'
    ' provided.',
)

# Agent specific.
_AGENT_NAME = flags.DEFINE_string('agent_name', 'm3a_gpt4v', help='Agent name.')

_FIXED_TASK_SEED = flags.DEFINE_boolean(
    'fixed_task_seed',
    False,
    'Whether to use the same task seed when running multiple task combinations'
    ' (n_task_combinations > 1).',
)
_MODEL_NAME = flags.DEFINE_string(
    'model_name',
    os.environ.get('OPENAI_MODEL_NAME', 'gpt-4-turbo-2024-04-09'),
    'Model name passed to the LLM wrapper. Defaults to OPENAI_MODEL_NAME env'
    ' var if set.',
)


# MiniWoB is very lightweight and new screens/View Hierarchy load quickly.
_MINIWOB_TRANSITION_PAUSE = 0.2

# Additional guidelines for the MiniWob tasks.
_MINIWOB_ADDITIONAL_GUIDELINES = [
    (
        'This task is running in a mock app, you must stay in this app and'
        ' DO NOT use the `navigate_home` action.'
    ),
]


def _get_agent(
    env: interface.AsyncEnv,
    family: str | None = None,
) -> base_agent.EnvironmentInteractingAgent:
  """Gets agent."""
  print('Initializing agent...')
  agent = None
  if _AGENT_NAME.value == 'human_agent':
    agent = human_agent.HumanAgent(env)
  elif _AGENT_NAME.value == 'random_agent':
    agent = random_agent.RandomAgent(env)
  # Gemini.
  elif _AGENT_NAME.value == 'm3a_gemini_gcp':
    agent = m3a.M3A(
        env, infer.GeminiGcpWrapper(model_name='gemini-1.5-pro-latest')
    )
  elif _AGENT_NAME.value == 't3a_gemini_gcp':
    agent = t3a.T3A(
        env, infer.GeminiGcpWrapper(model_name='gemini-1.5-pro-latest')
    )
  # GPT.
  elif _AGENT_NAME.value == 't3a_gpt4':
    agent = t3a.T3A(env, infer.Gpt4Wrapper(_MODEL_NAME.value))
  elif _AGENT_NAME.value == 'm3a_gpt4v':
    agent = m3a.M3A(env, infer.Gpt4Wrapper(_MODEL_NAME.value))
  # SeeAct.
  elif _AGENT_NAME.value == 'seeact':
    agent = seeact.SeeAct(env)

  if not agent:
    raise ValueError(f'Unknown agent: {_AGENT_NAME.value}')

  if (
      agent.name in ['M3A', 'T3A', 'SeeAct']
      and family
      and family.startswith('miniwob')
      and hasattr(agent, 'set_task_guidelines')
  ):
    agent.set_task_guidelines(_MINIWOB_ADDITIONAL_GUIDELINES)
  agent.name = _AGENT_NAME.value

  return agent


def _main() -> None:
  """Runs eval suite and gets rewards back."""
  a11y_method = (
      android_world_controller.A11yMethod(_A11Y_METHOD.value)
      if _A11Y_METHOD.value
      else None
  )

  device_mode = _DEVICE_MODE.value
  agentruntime_cfg = None
  if device_mode == env_launcher.DeviceMode.AGENTRUNTIME.value:
    agentruntime_cfg = agentruntime_config_lib.AgentRuntimeConfig.from_flags()
    # --sandboxes_yaml implies parallel mode: override execution_mode so
    # callers don't have to remember to also pass --execution_mode=parallel.
    if agentruntime_cfg.sandbox_ids_file:
      agentruntime_cfg.execution_mode = 'parallel'
      # Derive sandbox_count from the yaml list (validation happens in
      # _run_parallel → cfg.validate() → get_sandbox_ids()).
      try:
        ids = agentruntime_cfg.get_sandbox_ids()
        agentruntime_cfg.sandbox_count = len(ids)
      except (FileNotFoundError, ValueError) as exc:
        print(f'[run.py] ERROR reading --sandboxes_yaml: {exc}')
        raise

  # ------------------------------------------------------------------
  # Parallel execution path (AgentRuntime + SandboxPool)
  # ------------------------------------------------------------------
  if (
      agentruntime_cfg is not None
      and agentruntime_cfg.execution_mode == 'parallel'
  ):
    _run_parallel(agentruntime_cfg)
    return

  # ------------------------------------------------------------------
  # Serial execution path (original behaviour)
  # ------------------------------------------------------------------
  env = env_launcher.load_and_setup_env(
      console_port=_DEVICE_CONSOLE_PORT.value,
      emulator_setup=_EMULATOR_SETUP.value,
      adb_path=_ADB_PATH.value,
      grpc_port=_GRPC_PORT.value,
      device_mode=device_mode,
      adb_device_name=_ADB_DEVICE_NAME.value or None,
      a11y_method=a11y_method,
      agentruntime_config=agentruntime_cfg,
  )

  n_task_combinations = _N_TASK_COMBINATIONS.value
  task_registry = registry.TaskRegistry()
  suite = suite_utils.create_suite(
      task_registry.get_registry(family=_SUITE_FAMILY.value),
      n_task_combinations=n_task_combinations,
      seed=_TASK_RANDOM_SEED.value,
      tasks=_TASKS.value,
      use_identical_params=_FIXED_TASK_SEED.value,
  )
  suite.suite_family = _SUITE_FAMILY.value

  agent = _get_agent(env, _SUITE_FAMILY.value)

  if _SUITE_FAMILY.value.startswith('miniwob'):
    # MiniWoB pages change quickly, don't need to wait for screen to stabilize.
    agent.transition_pause = _MINIWOB_TRANSITION_PAUSE
  else:
    agent.transition_pause = None

  if _CHECKPOINT_DIR.value:
    checkpoint_dir = _CHECKPOINT_DIR.value
  else:
    checkpoint_dir = checkpointer_lib.create_run_directory(_OUTPUT_PATH.value)

  print(
      f'Starting eval with agent {_AGENT_NAME.value} and writing to'
      f' {checkpoint_dir}'
  )
  suite_utils.run(
      suite,
      agent,
      checkpointer=checkpointer_lib.IncrementalCheckpointer(checkpoint_dir),
      demo_mode=False,
  )
  print(
      f'Finished running agent {_AGENT_NAME.value} on {_SUITE_FAMILY.value}'
      f' family. Wrote to {checkpoint_dir}.'
  )
  env.close()


def _run_parallel(agentruntime_cfg) -> None:
  """Run the benchmark in parallel mode using SandboxPool + ParallelRunner.

  This function is entered when ``--device_mode=agentruntime`` AND
  ``--execution_mode=parallel``.  It:

  1. Creates the task suite (no live env required — tasks are pre-parameterised).
  2. Warms up a ``SandboxPool`` (creates *sandbox_count* E2B sandboxes in
     parallel).
  3. Builds a ``ParallelRunner`` wired to an ``agent_factory`` closure that
     re-creates a fresh agent for every task instance.
  4. Runs all task instances and reports aggregate success rate.
  5. Cleans up the pool.

  Args:
    agentruntime_cfg: A validated ``AgentRuntimeConfig`` with
      ``execution_mode='parallel'``.
  """
  # Lazy imports so these heavy modules are not loaded for serial runs.
  from android_world.runtime import parallel_runner as parallel_runner_lib  # pylint: disable=g-import-not-at-top
  from android_world.runtime import sandbox_pool as sandbox_pool_lib  # pylint: disable=g-import-not-at-top
  from android_world.runtime.parallel_runner import TaskInstance  # pylint: disable=g-import-not-at-top

  agentruntime_cfg.validate()

  # ---- build suite (no env needed for param generation) ----
  task_registry = registry.TaskRegistry()
  suite = suite_utils.create_suite(
      task_registry.get_registry(family=_SUITE_FAMILY.value),
      n_task_combinations=_N_TASK_COMBINATIONS.value,
      seed=_TASK_RANDOM_SEED.value,
      tasks=_TASKS.value,
      use_identical_params=_FIXED_TASK_SEED.value,
  )
  suite.suite_family = _SUITE_FAMILY.value

  # Flatten Suite → list[TaskInstance] preserving (task_name, seed) tuples.
  instances: list[TaskInstance] = []
  for task_name, task_list in suite.items():
    for task_obj in task_list:
      seed = task_obj.params.get('seed', 0) or 0
      instances.append(TaskInstance(
          task_name=task_name,
          task=task_obj,
          seed=seed,
      ))

  print(
      f'[Parallel] {len(instances)} task instance(s) across '
      f'{agentruntime_cfg.sandbox_count} sandbox(es).  '
      f'Agent: {_AGENT_NAME.value}.'
  )

  # ---- checkpoint dir ----
  if _CHECKPOINT_DIR.value:
    checkpoint_dir = _CHECKPOINT_DIR.value
  else:
    checkpoint_dir = checkpointer_lib.create_run_directory(_OUTPUT_PATH.value)
  print(f'[Parallel] Writing results to {checkpoint_dir}')
  checkpointer = checkpointer_lib.IncrementalCheckpointer(checkpoint_dir)

  # ---- warm up sandbox pool ----
  pool = sandbox_pool_lib.SandboxPool(
      cfg=agentruntime_cfg,
      artifacts_root=agentruntime_cfg.dump_artifacts_dir or checkpoint_dir,
  )
  try:
    pool.warm_up()
  except RuntimeError as exc:
    print(f'[Parallel] ERROR: sandbox pool warm-up failed: {exc}')
    raise

  # ---- agent factory ----
  # The factory is called inside each worker thread with the per-sandbox env.
  # _get_agent() is not directly reusable here because it also applies
  # miniwob-specific transition_pause; we replicate that logic inline.
  family = _SUITE_FAMILY.value

  def agent_factory(env):
    agent = _get_agent(env, family)
    if family.startswith('miniwob'):
      agent.transition_pause = _MINIWOB_TRANSITION_PAUSE
    else:
      agent.transition_pause = None
    return agent

  # ---- run ----
  runner = parallel_runner_lib.ParallelRunner(
      pool=pool,
      agent_factory=agent_factory,
      cfg=agentruntime_cfg,
      checkpointer=checkpointer,
  )

  try:
    results = runner.run(instances)
  finally:
    pool.close_all()

  # ---- report ----
  n_total = len(results)
  n_success = sum(1 for r in results if r.is_successful == 1.0)
  n_error = sum(1 for r in results if r.error)

  # Write details.json — one entry per task instance.
  import json as _json  # pylint: disable=g-import-not-at-top
  import datetime as _datetime  # pylint: disable=g-import-not-at-top

  details = [r.to_dict() for r in results]

  summary = {
      'timestamp': _datetime.datetime.now().isoformat(),
      'suite_family': _SUITE_FAMILY.value,
      'agent_name': _AGENT_NAME.value,
      'n_total': n_total,
      'n_success': n_success,
      'n_error': n_error,
      'success_rate': round(n_success / n_total, 4) if n_total else 0.0,
      'sandbox_count': agentruntime_cfg.sandbox_count,
      'sandboxes': sorted({r.sandbox_id for r in results}),
      'checkpoint_dir': checkpoint_dir,
  }

  try:
    with open(
        os.path.join(checkpoint_dir, 'summary.json'), 'w', encoding='utf-8'
    ) as f:
      _json.dump(summary, f, indent=2, default=str)
    with open(
        os.path.join(checkpoint_dir, 'details.json'), 'w', encoding='utf-8'
    ) as f:
      _json.dump(details, f, indent=2, default=str)
  except OSError as exc:
    logging.warning('Could not write summary/details JSON: %s', exc)

  # Console summary table.
  print(
      f'\n[Parallel] ─── Benchmark complete ───────────────────────────\n'
      f'  Suite      : {_SUITE_FAMILY.value}\n'
      f'  Agent      : {_AGENT_NAME.value}\n'
      f'  Tasks      : {n_total}  |  Success: {n_success}  |  Errors: {n_error}\n'
      f'  Rate       : {summary["success_rate"]:.1%}\n'
      f'  Output dir : {checkpoint_dir}\n'
      f'    ├─ details.json   (per-task results)\n'
      f'    ├─ summary.json   (aggregate stats)\n'
      f'    └─ <sandbox_id>/  (per-sandbox logs & screenshots)\n'
      f'──────────────────────────────────────────────────────────────'
  )


def main(argv: Sequence[str]) -> None:
  del argv
  _main()


if __name__ == '__main__':
  app.run(main)
