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
  sandbox_idx: int = 0  # Which sandbox this instance is assigned to.


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

    Instances are grouped by ``sandbox_idx``.  Each group is executed
    sequentially on a dedicated sandbox, while different sandboxes run in
    parallel.

    Args:
      instances: Flat list of ``TaskInstance`` objects to evaluate.  Each
        instance must have a ``sandbox_idx`` indicating which sandbox it
        belongs to.

    Returns:
      List of ``EpisodeResult``, one per instance, in completion order.
    """
    from collections import defaultdict  # pylint: disable=g-import-not-at-top

    # Group instances by their assigned sandbox.
    per_sandbox: dict[int, list[TaskInstance]] = defaultdict(list)
    for inst in instances:
      per_sandbox[inst.sandbox_idx].append(inst)

    n_sandboxes = len(per_sandbox)

    logging.info(
        'ParallelRunner: running %d task instance(s) across %d sandbox(es) '
        '(%d per sandbox).',
        len(instances),
        n_sandboxes,
        len(instances) // max(n_sandboxes, 1),
    )

    results: list[EpisodeResult] = []
    futures: dict[Future, int] = {}

    with ThreadPoolExecutor(
        max_workers=n_sandboxes, thread_name_prefix='par-runner'
    ) as ex:
      for sandbox_idx, sandbox_instances in per_sandbox.items():
        fut = ex.submit(self._run_sandbox, sandbox_idx, sandbox_instances)
        futures[fut] = sandbox_idx

      for fut in as_completed(futures):
        sandbox_idx = futures[fut]
        exc = fut.exception()
        if exc:
          logging.error(
              'ParallelRunner: sandbox %d encountered unhandled exception: %s',
              sandbox_idx,
              exc,
          )
          # Mark all instances for this sandbox as failed.
          for inst in per_sandbox[sandbox_idx]:
            results.append(EpisodeResult(
                task_name=inst.task_name,
                seed=inst.seed,
                is_successful=0.0,
                episode_length=0,
                error=str(exc),
            ))
        else:
          results.extend(fut.result())

    logging.info(
        'ParallelRunner: finished.  %d/%d successful.',
        sum(1 for r in results if r.is_successful == 1.0),
        len(results),
    )
    return results

  # -------------------------------------------------------------------------
  # Worker
  # -------------------------------------------------------------------------

  def _run_sandbox(
      self, sandbox_idx: int, instances: list[TaskInstance]
  ) -> list[EpisodeResult]:
    """Run all tasks assigned to one sandbox sequentially.

    Acquires the sandbox once and holds it for the entire task list, avoiding
    repeated acquire/release overhead and ensuring each sandbox runs its full
    suite independently.
    """
    results: list[EpisodeResult] = []
    with self._pool.acquire() as handle:
      logging.info(
          'Sandbox %d (%s): starting %d task(s).',
          sandbox_idx,
          handle.sandbox_id,
          len(instances),
      )
      for inst in instances:
        result = self._run_one_with_handle(inst, handle)
        results.append(result)
      logging.info(
          'Sandbox %d (%s): finished %d task(s), %d successful.',
          sandbox_idx,
          handle.sandbox_id,
          len(results),
          sum(1 for r in results if r.is_successful == 1.0),
      )
    return results

  def _run_one_with_handle(
      self, inst: TaskInstance, handle
  ) -> EpisodeResult:
    """Run a single task instance on a pre-acquired sandbox handle."""
    t_start = time.monotonic()

    sandbox_id = handle.sandbox_id
    env = handle.env
    writer = handle.artifact_writer

    writer.begin_task(inst.task_name, inst.seed)
    agent = self._agent_factory(env)
    episode_length = 0
    error_msg = None
    is_successful = 0.0
    max_reconnects = getattr(self._cfg, 'appium_reconnect_retries', 2)
    reconnect_count = 0

    try:
      # Reset env to home screen (with reconnection support).
      self._call_with_reconnect(
          lambda: env.reset(go_home=True),
          handle, max_reconnects, 'reset',
      )

      # Initialize the task.
      self._call_with_reconnect(
          lambda: inst.task.initialize_task(env),
          handle, max_reconnects, 'initialize_task',
      )

      writer.write_console(f'[START] {inst.task_name} seed={inst.seed}\n')
      writer.write_console(f'Goal: {inst.task.goal}\n')

      # Run the agent loop.
      max_steps = int(inst.task.complexity * 10)
      is_done = False
      for step in range(max_steps):
        try:
          response = agent.step(inst.task.goal)
        except Exception as step_exc:
          if self._is_session_dead(step_exc) and reconnect_count < max_reconnects:
            reconnect_count += 1
            logging.warning(
                'ParallelRunner: session death in agent.step() '
                '(task=%s, step=%d, attempt=%d/%d). Reconnecting…',
                inst.task_name,
                step,
                reconnect_count,
                max_reconnects,
            )
            writer.write_console(
                f'[RECONNECT] Session died at step {step+1}, '
                f'attempt {reconnect_count}/{max_reconnects}\n'
            )
            handle.controller.refresh_env()
            agent = self._agent_factory(env)
            # Retry this step.
            try:
              response = agent.step(inst.task.goal)
            except Exception as retry_exc:
              if self._is_session_dead(retry_exc):
                raise RuntimeError(
                    f'Session still dead after reconnection: {retry_exc}'
                ) from retry_exc
              raise
          else:
            raise

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

      # Evaluate success (with reconnection support).
      is_successful = self._call_with_reconnect(
          lambda: inst.task.is_successful(env),
          handle, max_reconnects, 'is_successful',
      )
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

  # -------------------------------------------------------------------------
  # Session death detection & reconnection helpers
  # -------------------------------------------------------------------------

  @staticmethod
  def _is_session_dead(exc: Exception) -> bool:
    """Return True if ``exc`` indicates the Appium session has died."""
    exc_str = str(exc).lower()
    exc_type = type(exc).__name__
    return (
        'InvalidSessionIdException' in exc_type
        or 'NoSuchDriverError' in exc_type
        or 'session is either terminated or not started' in exc_str
        or 'invalid session id' in exc_str
        or 'invalid session' in exc_str
    )

  def _call_with_reconnect(self, fn, handle, max_retries: int, label: str):
    """Call ``fn()`` and retry once with reconnection on session death.

    Args:
      fn: Zero-arg callable to execute.
      handle: The ``SandboxHandle`` (for calling ``refresh_env``).
      max_retries: Maximum reconnection attempts.
      label: Description for log messages (e.g. 'reset', 'is_successful').

    Returns:
      The return value of ``fn()``.

    Raises:
      RuntimeError: If reconnection fails or retries are exhausted.
      Other exceptions: Non-session errors propagate unchanged.
    """
    try:
      return fn()
    except Exception as exc:
      if not self._is_session_dead(exc) or max_retries <= 0:
        raise
      logging.warning(
          'ParallelRunner: session death during %s (sandbox=%s). '
          'Attempting reconnection…',
          label,
          handle.sandbox_id,
      )
      handle.controller.refresh_env()
      # Retry once after successful reconnection.
      return fn()

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
    # IncrementalCheckpointer key: include sandbox_id to avoid collisions
    # when the same (task, seed) runs on multiple sandboxes.
    instance_key = f'{result.task_name}_{result.seed}_{result.sandbox_id}'
    with self._checkpoint_lock:
      try:
        self._checkpointer.save_episodes([episode], instance_key)
      except Exception as exc:  # pylint: disable=broad-except
        logging.warning('ParallelRunner: checkpoint save failed: %s', exc)
