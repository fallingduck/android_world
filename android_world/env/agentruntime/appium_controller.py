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

"""AppiumController — AndroidWorldController-compatible wrapper for Appium.

This module provides ``AppiumController``, the AgentRuntime equivalent of
``AndroidWorldController``.  It inherits from ``base_wrapper.BaseWrapper``
so that ``AsyncAndroidEnv`` can consume it without modification.

Key design decisions (see also docs/agentruntime_sandbox_integration_plan.md,
section 16.2 and 16.3):

1. **Inherits BaseWrapper** — ``AsyncAndroidEnv`` calls ``controller.reset()``
   and ``controller.step(no_op_action)`` which route through BaseWrapper's
   ``reset()`` / ``step()`` → ``_process_timestep()`` chain.  By overriding
   ``_process_timestep`` we inject ``forest=None`` and ``ui_elements`` derived
   from uiautomator XML into the observation dict, exactly as
   ``AndroidWorldController`` does.

2. **Forced UIAUTOMATOR** — The ``A11Y_FORWARDER_APP`` method requires a gRPC
   connection and a custom APK that are not available in the AgentRuntime
   Appium-only pipeline.  We always use ``A11yMethod.UIAUTOMATOR`` (uiautomator
   dump over ``mobile: shell``).

3. **execute_adb_call delegation** — All ``adb_utils.*`` calls hit
   ``execute_adb_call`` which is implemented by the ``AppiumAdbBridge`` mixin.
   The bridge translates every ``AdbRequest`` variant into an Appium or
   ``mobile: shell`` call.

4. **``_env`` attribute** — ``BaseWrapper.__getattr__`` delegates unknown
   attribute access to ``self._env``.  We set ``self._env`` to a thin
   ``_AppiumEnvAdapter`` that satisfies the ``AndroidEnvInterface`` contract
   (action_spec, observation_spec, reset, step, execute_adb_call, close).

Factory
-------
Use ``create_appium_controller(cfg)`` — it builds the full
Session → Driver → AppiumEnv → AppiumController stack.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any, Optional

from absl import logging
from android_env import env_interface
from android_env.components import action_type
from android_env.proto import adb_pb2
from android_env.wrappers import base_wrapper
from android_world.env import android_world_controller
from android_world.env import representation_utils
from android_world.env.agentruntime import appium_adb_bridge
from android_world.env.agentruntime import appium_connection
from android_world.env.agentruntime import appium_env as appium_env_lib
from android_world.env.agentruntime import config as config_lib
from android_world.env.agentruntime import session as session_lib
from android_world.utils import file_utils
import dm_env
from dm_env import specs
import numpy as np


# ---------------------------------------------------------------------------
# Thin AndroidEnvInterface adapter around AppiumEnv
# ---------------------------------------------------------------------------

class _AppiumEnvAdapter(
    appium_adb_bridge.AppiumAdbBridge,
    env_interface.AndroidEnvInterface,
):
  """Bridges AppiumEnv to the AndroidEnvInterface contract.

  ``BaseWrapper`` requires ``self._env`` to be an ``AndroidEnvInterface``.
  This adapter satisfies that contract while delegating device calls to
  ``AppiumEnv`` (through the ``AppiumAdbBridge`` mixin).
  """

  def __init__(self, appium_env: appium_env_lib.AppiumEnv):
    self._appium_env = appium_env  # consumed by AppiumAdbBridge

  # -- AndroidEnvInterface abstract methods --

  def action_spec(self) -> dict[str, specs.Array]:
    return {
        'action_type': specs.Array(shape=(), dtype=np.int32),
        'touch_position': specs.Array(shape=(2,), dtype=np.float32),
    }

  def observation_spec(self) -> dict[str, specs.Array]:
    h, w = 1080, 1920  # Placeholder; updated after first screenshot.
    return {
        'pixels': specs.Array(shape=(h, w, 3), dtype=np.uint8),
    }

  def reset(self) -> dm_env.TimeStep:
    pixels = self._appium_env.screenshot()
    return dm_env.restart({'pixels': pixels})

  def step(self, action: dict[str, np.ndarray]) -> dm_env.TimeStep:
    # The controller calls step with a no-op action just to get a new
    # observation.  We just take a fresh screenshot.
    pixels = self._appium_env.screenshot()
    return dm_env.transition(
        reward=0.0,
        observation={'pixels': pixels},
        discount=1.0,
    )

  def close(self) -> None:
    self._appium_env.quit()

  @property
  def raw_action(self):
    return None

  @property
  def raw_observation(self):
    return {'pixels': np.zeros((1080, 1920, 3), dtype=np.uint8)}

  def stats(self) -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# AppiumController
# ---------------------------------------------------------------------------

class AppiumController(
    appium_adb_bridge.AppiumAdbBridge,
    base_wrapper.BaseWrapper,
):
  """AndroidWorldController-compatible controller backed by Appium.

  Inherits from:
    - ``AppiumAdbBridge`` for ``execute_adb_call()``
    - ``BaseWrapper`` for the dm_env wrapper chain used by ``AsyncAndroidEnv``

  ``self._env`` is an ``_AppiumEnvAdapter`` instance.
  ``self._appium_env`` is the underlying ``AppiumEnv`` (used by the bridge).
  """

  def __init__(
      self,
      appium_env: appium_env_lib.AppiumEnv,
      session: session_lib.SandboxSession,
  ):
    self._appium_env = appium_env  # used by AppiumAdbBridge
    self._session = session
    self._device_mode = 'agentruntime'

    adapter = _AppiumEnvAdapter(appium_env)
    # BaseWrapper.__init__ sets self._env = adapter.
    base_wrapper.BaseWrapper.__init__(self, adapter)

  # -------------------------------------------------------------------------
  # _process_timestep — inject UI tree into observation
  # -------------------------------------------------------------------------

  def _process_timestep(self, timestep: dm_env.TimeStep) -> dm_env.TimeStep:
    """Add ``forest`` (None) and ``ui_elements`` to the observation dict.

    ``AsyncAndroidEnv._process_timestep`` expects both keys in the observation.
    We use UIAUTOMATOR (mobile: shell uiautomator dump) to obtain the XML and
    parse it with ``representation_utils.xml_dump_to_ui_elements``.
    """
    try:
      xml = self._appium_env.page_source()
      ui_elements = representation_utils.xml_dump_to_ui_elements(xml)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'AppiumController: failed to get UI elements: %s', exc
      )
      ui_elements = []

    timestep.observation[
        android_world_controller.OBSERVATION_KEY_FOREST
    ] = None
    timestep.observation[
        android_world_controller.OBSERVATION_KEY_UI_ELEMENTS
    ] = ui_elements
    return timestep

  # -------------------------------------------------------------------------
  # Properties required by AndroidWorldController contract
  # -------------------------------------------------------------------------

  @property
  def device_screen_size(self) -> tuple[int, int]:
    """Physical screen size (same as logical for Appium)."""
    return self._appium_env.window_size()

  @property
  def logical_screen_size(self) -> tuple[int, int]:
    return self._appium_env.window_size()

  @property
  def env(self) -> env_interface.AndroidEnvInterface:
    """The underlying AndroidEnvInterface (the adapter)."""
    return self._env

  @property
  def device_mode(self) -> str:
    return self._device_mode

  # -------------------------------------------------------------------------
  # UI element access
  # -------------------------------------------------------------------------

  def get_ui_elements(self) -> list[representation_utils.UIElement]:
    """Return UI elements parsed from the current uiautomator XML dump."""
    try:
      xml = self._appium_env.page_source()
      return representation_utils.xml_dump_to_ui_elements(xml)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning('AppiumController.get_ui_elements failed: %s', exc)
      return []

  # -------------------------------------------------------------------------
  # File operations  (match AndroidWorldController.pull_file / push_file)
  # -------------------------------------------------------------------------

  def pull_file(
      self,
      remote_db_file_path: str,
      timeout_sec: Optional[float] = None,
  ) -> contextlib._GeneratorContextManager:
    """Pull the file at ``remote_db_file_path`` (and its siblings) to a temp dir.

    Mirrors the ``AndroidWorldController.pull_file`` contract: returns a
    context manager that yields the path to a local temp directory containing
    the pulled file.  The directory is cleaned up on exit.

    Uses Appium's native ``pull_file`` API (via UiAutomator2) to directly
    retrieve the file, bypassing the ``mobile: shell ls`` enumeration that
    fails on SELinux-protected directories (e.g. VLC's ``app_db``).

    Args:
      remote_db_file_path: Path to a file on the device.
      timeout_sec: Unused (kept for API compatibility).

    Returns:
      A context manager yielding the local temp directory path.
    """
    return self._pull_file_ctx(remote_db_file_path)

  @contextlib.contextmanager
  def _pull_file_ctx(self, remote_file_path: str):
    """Context manager: pull a single file (+ WAL/SHM) to a temp dir."""
    import shutil  # pylint: disable=g-import-not-at-top
    tmp_dir = tempfile.mkdtemp(prefix='aw_pull_')
    try:
      filename = os.path.basename(remote_file_path)
      local_path = os.path.join(tmp_dir, filename)
      try:
        data = self._appium_env.pull_file(remote_file_path)
      except Exception as exc:  # pylint: disable=broad-except
        # Appium raises UnknownException (or similar) when the remote file does
        # not exist (e.g. VLC's vlc_media.db before first launch).  Convert to
        # FileNotFoundError so callers like sqlite_utils.table_exists() can
        # treat a missing file uniformly without depending on Appium internals.
        raise FileNotFoundError(
            f'Remote file not found: {remote_file_path}'
        ) from exc
      with open(local_path, 'wb') as fh:
        fh.write(data)

      # Also pull WAL and SHM companion files for SQLite WAL-mode databases.
      # Room (Android ORM) uses WAL by default; table DDL may reside in the
      # WAL file until a checkpoint flushes it to the main DB file.
      for suffix in ('-wal', '-shm'):
        companion = remote_file_path + suffix
        try:
          companion_data = self._appium_env.pull_file(companion)
          with open(os.path.join(tmp_dir, filename + suffix), 'wb') as fh:
            fh.write(companion_data)
        except Exception:  # pylint: disable=broad-except
          pass  # Companion may not exist (journal mode or already checkpointed).

      yield tmp_dir
    finally:
      shutil.rmtree(tmp_dir, ignore_errors=True)

  @contextlib.contextmanager
  def _pull_directory_ctx(self, remote_dir: str):
    """Context manager: pull remote_dir to a temp dir, yield it, then clean up."""
    import shutil  # pylint: disable=g-import-not-at-top
    tmp_dir = tempfile.mkdtemp(prefix='aw_pull_')
    try:
      self._appium_env.pull_directory(remote_dir, tmp_dir)
      yield tmp_dir
    finally:
      shutil.rmtree(tmp_dir, ignore_errors=True)

  def push_file(
      self,
      local_db_file_path: str,
      remote_db_file_path: str,
      timeout_sec: Optional[float] = None,
  ) -> None:
    """Push a local file to the device, clearing the remote directory first.

    Mirrors the ``AndroidWorldController.push_file`` contract.

    Args:
      local_db_file_path: Local source file.
      remote_db_file_path: Destination path on the device.
      timeout_sec: Unused (kept for API compatibility).
    """
    remote_dir = os.path.dirname(remote_db_file_path)
    # Clear the remote directory first (same as AndroidWorldController).
    self._appium_env.shell(
        f'rm -rf "{remote_dir}" && mkdir -p "{remote_dir}"'
    )
    self._appium_env.push_file(local_db_file_path, remote_db_file_path)

  # -------------------------------------------------------------------------
  # Reconnect (session recovery after InvalidSessionIdException)
  # -------------------------------------------------------------------------

  def refresh_env(self) -> None:
    """Reconnect the Appium driver after session death.

    Creates a new WebDriver session using the same sandbox metadata.  If
    reconnection fails, raises ``RuntimeError`` so the caller can decide
    whether to abort or retry.

    Raises:
      RuntimeError: If the driver could not be re-established.
    """
    logging.warning(
        'AppiumController.refresh_env: reconnecting (sandbox=%s).',
        self._session.sandbox_id,
    )
    success = self._appium_env.reconnect_driver(self._session)
    if not success:
      raise RuntimeError(
          f'AppiumController.refresh_env failed for sandbox '
          f'{self._session.sandbox_id}'
      )

  # -------------------------------------------------------------------------
  # A11y tree (not available in Appium-only mode)
  # -------------------------------------------------------------------------

  def get_a11y_forest(self):
    """Returns None — A11Y_FORWARDER_APP is not available over Appium."""
    return None

  # -------------------------------------------------------------------------
  # Lifecycle
  # -------------------------------------------------------------------------

  def close(self) -> None:
    """Quit the Appium driver and close the sandbox session."""
    try:
      self._appium_env.quit()
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning('AppiumController.close: driver quit error: %s', exc)
    try:
      self._session.close()
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning('AppiumController.close: session close error: %s', exc)

  def __repr__(self) -> str:
    return (
        f'AppiumController(sandbox={self._session.sandbox_id!r},'
        f' appium={self._session.appium_url!r})'
    )


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def create_appium_controller(
    cfg: config_lib.AgentRuntimeConfig,
) -> AppiumController:
  """Build the full Session → Driver → AppiumEnv → AppiumController stack.

  This is the function called by ``env_launcher._get_env`` when
  ``device_mode=AGENTRUNTIME``.

  Args:
    cfg: A validated ``AgentRuntimeConfig``.

  Returns:
    A ready-to-use ``AppiumController`` instance.
  """
  # 1. Create / connect sandbox session.
  sandbox_session = session_lib.SandboxSession.from_config(cfg)

  # 2. Create an isolated Appium driver for this sandbox.
  driver = appium_connection.create_appium_driver(sandbox_session)

  # 3. Wrap driver in AppiumEnv.
  env = appium_env_lib.AppiumEnv(
      driver=driver,
      shell_timeout=cfg.appium_shell_timeout,
      sandbox_id=sandbox_session.sandbox_id,
  )

  # 4. Build and return the controller.
  controller = AppiumController(appium_env=env, session=sandbox_session)
  logging.info('AppiumController ready: %r', controller)
  return controller
