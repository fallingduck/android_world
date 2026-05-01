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

"""Multi-sandbox resource pool for parallel AndroidWorld evaluation.

``SandboxPool`` manages a collection of ``(SandboxSession, AppiumController,
AsyncEnv)`` triples.  Callers ``acquire()`` a handle before running a task and
``release()`` it afterwards so the slot can be reused.

Design principles:

- **Token isolation** — each sandbox has its own Appium driver and connection
  class.  ``acquire/release`` is guarded by a ``threading.Semaphore`` so the
  pool never over-commits.
- **Lazy creation** — sandboxes are created concurrently in a
  ``ThreadPoolExecutor`` during ``warm_up()``, not at construction time.  This
  makes the pool creation time proportional to the slowest sandbox, not the
  sum of all sandbox startup times.
- **Graceful cleanup** — ``close_all()`` iterates every slot, quits the driver,
  and kills / releases the sandbox regardless of whether the slot is currently
  in use.
- **Diagnostic artefacts** — each ``SandboxHandle`` carries an
  ``ArtifactWriter`` for structured per-sandbox output.

Usage::

  pool = SandboxPool(cfg)
  pool.warm_up()
  try:
    with pool.acquire() as handle:
      handle.env.reset(go_home=True)
      # ... run task ...
  finally:
    pool.close_all()
"""

from __future__ import annotations

import contextlib
import dataclasses
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator, Optional

from absl import logging

from android_world.env import env_launcher
from android_world.env import interface
from android_world.env.agentruntime import appium_controller as ac_lib
from android_world.env.agentruntime import config as config_lib
from android_world.env.agentruntime import session as session_lib
from android_world.runtime import artifacts as artifacts_lib


@dataclasses.dataclass
class SandboxHandle:
  """A leased sandbox environment.

  Attributes:
    sandbox_id: E2B sandbox identifier.
    env: The ``AsyncAndroidEnv`` bound to this sandbox.
    controller: The ``AppiumController`` for this sandbox.
    session: The ``SandboxSession`` for lifecycle management.
    artifact_writer: Per-sandbox artefact output helper.
    slot_index: Zero-based index in the pool (for logging).
  """

  sandbox_id: str
  env: interface.AsyncEnv
  controller: ac_lib.AppiumController
  session: session_lib.SandboxSession
  artifact_writer: artifacts_lib.ArtifactWriter
  slot_index: int


class _PoolSlot:
  """Internal bookkeeping for one sandbox slot."""

  def __init__(self):
    self.handle: Optional[SandboxHandle] = None
    self.in_use: bool = False
    self.lock = threading.Lock()


class SandboxPool:
  """Thread-safe pool of Android sandbox environments.

  Args:
    cfg: ``AgentRuntimeConfig`` used to create / connect sandboxes.
      ``cfg.sandbox_count`` determines the pool size.
    artifacts_root: Root directory for per-sandbox artefact output.  Each
      sandbox gets a sub-directory ``<artifacts_root>/sandbox_<i>/``.
  """

  def __init__(
      self,
      cfg: config_lib.AgentRuntimeConfig,
      artifacts_root: Optional[str] = None,
  ):
    self._cfg = cfg
    self._size = cfg.sandbox_count
    self._artifacts_root = artifacts_root or cfg.dump_artifacts_dir
    self._slots: list[_PoolSlot] = [_PoolSlot() for _ in range(self._size)]
    # Semaphore tracks the number of *available* (idle) slots.
    self._semaphore = threading.Semaphore(0)  # starts at 0; incremented in warm_up
    self._closed = False

  # -------------------------------------------------------------------------
  # Lifecycle
  # -------------------------------------------------------------------------

  def warm_up(self) -> None:
    """Create all sandboxes concurrently and populate the pool.

    Uses a ``ThreadPoolExecutor`` so sandbox creation (which involves
    network round-trips to E2B) happens in parallel.

    Raises:
      RuntimeError: If any sandbox fails to start.
    """
    logging.info(
        'SandboxPool: warming up %d sandbox(es)…', self._size
    )
    max_workers = min(self._size, 8)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='pool-warm') as ex:
      futures = {
          ex.submit(self._init_slot, i): i for i in range(self._size)
      }
      errors = []
      for fut in as_completed(futures):
        idx = futures[fut]
        exc = fut.exception()
        if exc:
          logging.error('SandboxPool: slot %d failed to start: %s', idx, exc)
          errors.append((idx, exc))
        else:
          self._semaphore.release()
          logging.info('SandboxPool: slot %d ready.', idx)

    if errors:
      raise RuntimeError(
          f'SandboxPool: {len(errors)} sandbox(es) failed to start: '
          + ', '.join(f'slot {i}: {e}' for i, e in errors)
      )
    logging.info('SandboxPool: all %d slot(s) ready.', self._size)

  def _init_slot(self, idx: int) -> None:
    """Create or connect one sandbox and store it in ``self._slots[idx]``.

    If ``cfg.sandbox_ids_file`` is set (or ``cfg.get_sandbox_ids()`` returns
    a non-empty list), the slot at index ``idx`` connects to the corresponding
    pre-created sandbox.  Otherwise a fresh sandbox is created from
    ``cfg.template``.
    """
    sandbox_ids = self._cfg.get_sandbox_ids()
    if sandbox_ids:
      # Connect path: use the id at position idx (wrap if fewer ids than slots).
      sid = sandbox_ids[idx % len(sandbox_ids)]
      slot_cfg = dataclasses.replace(self._cfg, sandbox_id=sid, template=None)
    else:
      # Create path: slot 0 may reuse an explicit sandbox_id, others create fresh.
      slot_cfg = dataclasses.replace(
          self._cfg,
          sandbox_id=self._cfg.sandbox_id if idx == 0 else None,
      )

    controller = ac_lib.create_appium_controller(slot_cfg)
    env = interface.AsyncAndroidEnv(controller)

    artefact_dir = None
    if self._artifacts_root:
      import os  # pylint: disable=g-import-not-at-top
      artefact_dir = os.path.join(self._artifacts_root, f'sandbox_{idx}')
      os.makedirs(artefact_dir, exist_ok=True)

    writer = artifacts_lib.ArtifactWriter(
        output_dir=artefact_dir,
        sandbox_id=controller._session.sandbox_id,
    )

    handle = SandboxHandle(
        sandbox_id=controller._session.sandbox_id,
        env=env,
        controller=controller,
        session=controller._session,
        artifact_writer=writer,
        slot_index=idx,
    )
    self._slots[idx].handle = handle

  # -------------------------------------------------------------------------
  # Acquire / release
  # -------------------------------------------------------------------------

  @contextlib.contextmanager
  def acquire(self, timeout: Optional[float] = None) -> Iterator[SandboxHandle]:
    """Context manager: lease one sandbox handle from the pool.

    Blocks until a slot is available.  Releases back to the pool on exit.

    Args:
      timeout: Max seconds to wait for a free slot.  ``None`` = wait forever.

    Yields:
      A ``SandboxHandle`` for the leased sandbox.

    Raises:
      RuntimeError: If no slot becomes available within ``timeout`` seconds.
    """
    acquired = self._semaphore.acquire(timeout=timeout)
    if not acquired:
      raise RuntimeError(
          f'SandboxPool: no free slot available after {timeout}s.'
      )

    # Find an idle slot and mark it in-use.
    slot = self._find_idle_slot()
    if slot is None or slot.handle is None:
      self._semaphore.release()
      raise RuntimeError('SandboxPool: internal error — no idle slot after semaphore acquire.')

    with slot.lock:
      slot.in_use = True

    try:
      yield slot.handle
    finally:
      with slot.lock:
        slot.in_use = False
      self._semaphore.release()

  def _find_idle_slot(self) -> Optional[_PoolSlot]:
    for slot in self._slots:
      with slot.lock:
        if not slot.in_use and slot.handle is not None:
          return slot
    return None

  # -------------------------------------------------------------------------
  # Cleanup
  # -------------------------------------------------------------------------

  def close_all(self) -> None:
    """Quit all Appium drivers and kill all sandbox sessions.

    Safe to call multiple times.
    """
    if self._closed:
      return
    self._closed = True
    logging.info('SandboxPool: closing %d slot(s)…', self._size)
    for i, slot in enumerate(self._slots):
      if slot.handle is not None:
        try:
          slot.handle.controller.close()
        except Exception as exc:  # pylint: disable=broad-except
          logging.warning(
              'SandboxPool: error closing slot %d: %s', i, exc
          )

  def kill_all(self) -> None:
    """Alias for ``close_all()`` — included for API symmetry."""
    self.close_all()

  def __len__(self) -> int:
    return self._size

  def __repr__(self) -> str:
    idle = sum(
        1 for s in self._slots if s.handle is not None and not s.in_use
    )
    return f'SandboxPool(size={self._size}, idle={idle})'
