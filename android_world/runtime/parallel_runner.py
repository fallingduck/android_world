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

"""Parallel task runner for AndroidWorld on AgentRuntime sandboxes.

``ParallelRunner`` distributes a list of ``TaskEval`` instances across a
``SandboxPool``.  Each task runs in a dedicated thread so that the blocking
Appium WebDriver calls don't stall other tasks.

Architecture
------------

  ParallelRunner.run(suite, agent_factory, checkpointer)
      │
      ├── flatten suite → list[TaskInstance]
      │
      ├── ThreadPoolExecutor (thread_pool_size workers)
      │       ├── worker thread: pool.acquire() → run one task → pool.release()
      │       └── ...
      │
      └── aggregate results → List[EpisodeResult]

Key design decisions
--------------------
- **One thread per concurrent task**.  Appium's WebDriver is *not* thread-safe
  for a shared session, but each sandbox has its own driver — so one
  thread-per-sandbox is safe.
- **Blocking I/O in threads**.  All Appium calls are synchronous; we don't
  pretend they are async.  ThreadPoolExecutor handles the scheduling.
- **agent_factory** is called inside each worker thread with the sandbox env.
  This ensures agents are not shared across threads.
- **Checkpointing** uses a threading.Lock to serialize JSON writes so
  concurrent threads don't corrupt the checkpoint file.
- **Artefacts on failure**.  When ``dump_logcat_on_failure=True``, the worker
  captures a screenshot + page_source + logcat before releasing the sandbox.

``EpisodeResult`` mirrors the dict format used by ``suite_utils.run`` so that
downstream reporting code can consume results from both runners.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from absl import logging

from android_world import checkpointer as checkpointer_lib
from android_world.agents import base_agent
from android_world.env import interface
from android_world.runtime import sandbox_pool as pool_lib
from android_world.task_evals import task_eval


@dataclasses.dataclass
class TaskInstance:
  """A single (task_class, params, seed) combination to be evaluated."""
  task_name: str
  task: task_eval.TaskEval
  seed: int


@dataclasses.dataclass
class EpisodeResult:
  """Result of running one task instance."""
  task_name: str
  seed: int
  is_successful: float  # 0.0 or 1.0 (or partial for composite tasks)
  episode_length: int
  error: Optional[str] = None
  sandbox_id: str = ''
  duration_secs: float = 0.0

  def to_dict(self) -> dict[str, Any]:
    return dataclasses.asdict(self)


AgentFactory = Callable[[interface.AsyncEnv], base_agent.EnvironmentInteractingAgent]


class ParallelRunner:
  """Runs task instances in parallel across a ``SandboxPool``.

  Args:
    pool: A warmed-up ``SandboxPool``.
    agent_factory: Callable that accepts an ``AsyncEnv`` and returns a fresh
      agent.  Called inside worker threads, so it must be thread-safe.
    cfg: ``AgentRuntimeConfig`` for concurrency / artefact settings.
    checkpointer: Optional ``IncrementalCheckpointer`` for fault-tolerant
      result saving.  Writes are serialized with a lock.
  """

  def __init__(
      self,
      pool: pool_lib.SandboxPool,
      agent_factory: AgentFactory,
      cfg,  # AgentRuntimeConfig
      checkpointer: Optional[checkpointer_lib.IncrementalCheckpointer] = None,
  ):
    self._pool = pool
    self._agent_factory = agent_factory
    self._cfg = cfg
    self._checkpointer = checkpointer
    self._checkpoint_lock = threading.Lock()

  # -------------------------------------------------------------------------
  # Public entry point
  # -------------------------------------------------------------------------

  def run(self, instances: list[TaskInstance]) -> list[EpisodeResult]:
    """Run all task instances and return aggregated results.

    Args:
      instances: Flat list of ``TaskInstance`` objects to evaluate.

    Returns:
      List of ``EpisodeResult``, one per instance, in completion order.
    """
    max_workers = min(
        self._cfg.thread_pool_size,
        self._cfg.max_concurrency or len(self._pool),
        len(instances),
    )
    max_workers = max(max_workers, 1)

    results: list[EpisodeResult] = []
    futures: dict[Future, TaskInstance] = {}

    logging.info(
        'ParallelRunner: running %d task instance(s) with %d worker thread(s).',
        len(instances),
        max_workers,
    )

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix='par-runner'
    ) as ex:
      for inst in instances:
        fut = ex.submit(self._run_one, inst)
        futures[fut] = inst

      for fut in as_completed(futures):
        inst = futures[fut]
        exc = fut.exception()
        if exc:
          logging.error(
              'ParallelRunner: unhandled exception for task %s seed %d: %s',
              inst.task_name,
              inst.seed,
              exc,
          )
          result = EpisodeResult(
              task_name=inst.task_name,
              seed=inst.seed,
              is_successful=0.0,
              episode_length=0,
              error=str(exc),
          )
        else:
          result = fut.result()
        results.append(result)

    logging.info(
        'ParallelRunner: finished.  %d/%d successful.',
        sum(1 for r in results if r.is_successful == 1.0),
        len(results),
    )
    return results

  # -------------------------------------------------------------------------
  # Worker
  # -------------------------------------------------------------------------

  def _run_one(self, inst: TaskInstance) -> EpisodeResult:
    """Run a single task instance inside a worker thread."""
    t_start = time.monotonic()

    with self._pool.acquire() as handle:
      sandbox_id = handle.sandbox_id
      env = handle.env
      writer = handle.artifact_writer

      writer.begin_task(inst.task_name, inst.seed)
      agent = self._agent_factory(env)
      episode_length = 0
      error_msg = None
      is_successful = 0.0

      try:
        # Reset env to home screen.
        env.reset(go_home=True)

        # Initialize the task.
        inst.task.initialize_task(env)

        writer.write_console(f'[START] {inst.task_name} seed={inst.seed}\n')
        writer.write_console(f'Goal: {inst.task.goal}\n')

        # Run the agent loop.
        max_steps = int(inst.task.complexity * 10)
        is_done = False
        for step in range(max_steps):
          response = agent.step(inst.task.goal)
          episode_length = step + 1
          # Save per-step screenshots if available (e.g. M3A agent).
          if response.data:
            for label in ('raw_screenshot', 'before_screenshot_with_som',
                          'after_screenshot_with_som'):
              pixels = response.data.get(label)
              if pixels is not None:
                writer.write_step_screenshot(pixels, step + 1, label)
          if response.done:
            is_done = True
            break

        # Evaluate success.
        is_successful = inst.task.is_successful(env)
        writer.write_console(
            f'[END] success={is_successful}, steps={episode_length}\n'
        )

      except Exception as exc:  # pylint: disable=broad-except
        error_msg = traceback.format_exc()
        logging.error(
            'ParallelRunner: task %s seed %d failed (sandbox=%s): %s',
            inst.task_name,
            inst.seed,
            sandbox_id,
            exc,
        )
        writer.write_console(f'[ERROR] {exc}\n')

        # Collect failure artefacts.
        if self._cfg.dump_logcat_on_failure:
          try:
            pixels = env.get_state().pixels
            xml = handle.controller._appium_env.page_source()
            logcat = handle.controller._appium_env.dump_logcat()
            writer.write_failure_bundle(
                pixels=pixels, xml=xml, logcat=logcat, error=error_msg
            )
          except Exception as art_exc:  # pylint: disable=broad-except
            logging.warning(
                'ParallelRunner: artefact collection failed for %s: %s',
                sandbox_id,
                art_exc,
            )

      finally:
        # Tear down task (best-effort).
        try:
          inst.task.tear_down(env)
        except Exception:  # pylint: disable=broad-except
          pass

        # Write result.
        duration = time.monotonic() - t_start
        result = EpisodeResult(
            task_name=inst.task_name,
            seed=inst.seed,
            is_successful=is_successful,
            episode_length=episode_length,
            error=error_msg,
            sandbox_id=sandbox_id,
            duration_secs=duration,
        )
        writer.write_result(result.to_dict())
        writer.end_task()

        # Checkpoint (serialised).
        if self._checkpointer is not None:
          self._save_checkpoint(result)

    return result

  def _save_checkpoint(self, result: EpisodeResult) -> None:
    """Write one result to the checkpointer (thread-safe).

    ``IncrementalCheckpointer.save_episodes`` stores a list of Episode dicts
    under a key built from ``task_name + '_' + seed``.  We mirror the field
    names used by ``suite_utils._run_task`` so that downstream
    ``process_episodes`` analysis works on both serial and parallel results.
    """
    from android_world import constants  # lazy import avoids circular dep  # pylint: disable=g-import-not-at-top

    episode: checkpointer_lib.Episode = {
        constants.EpisodeConstants.TASK_TEMPLATE: result.task_name,
        constants.EpisodeConstants.IS_SUCCESSFUL: result.is_successful,
        constants.EpisodeConstants.EPISODE_LENGTH: result.episode_length,
        constants.EpisodeConstants.EXCEPTION_INFO: result.error or None,
        constants.EpisodeConstants.SEED: result.seed,
        # Parallel-mode-specific extras (not present in serial checkpoints but
        # harmless for downstream processing).
        'sandbox_id': result.sandbox_id,
        'duration_secs': result.duration_secs,
    }
    # IncrementalCheckpointer key: "<task_name>_<seed>" so each (task, seed)
    # combination gets its own file and can be resumed independently.
    instance_key = f'{result.task_name}_{result.seed}'
    with self._checkpoint_lock:
      try:
        self._checkpointer.save_episodes([episode], instance_key)
      except Exception as exc:  # pylint: disable=broad-except
        logging.warning('ParallelRunner: checkpoint save failed: %s', exc)
