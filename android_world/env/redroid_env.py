"""AndroidEnv-compatible wrapper for a redroid device reachable over ADB."""

from __future__ import annotations

import subprocess
from typing import Any

from absl import logging
from android_env import env_interface
from android_env.components import adb_call_parser
from android_env.components import adb_controller
from android_env.components import action_type
from android_env.components import config_classes
from android_env.components import errors as adb_errors
from android_env.proto import adb_pb2
import cv2
import dm_env
from dm_env import specs
import numpy as np


# Substrings that indicate the adb server has silently dropped the TCP
# forwarded device (typical after long idle or after a task explodes and
# leaves the daemon in a bad state). ONLY these errors should trigger a
# reconnect — reconnecting on unrelated errors (e.g. a shell command that
# legitimately failed) has been observed to actively *break* a working
# connection in some cases.
_OFFLINE_MARKERS = (
    b"device '",  # e.g. "adb: device 'localhost:25022' not found"
    b'not found',
    b'device offline',
    b'no devices/emulators found',
    b'closed',
    b'connection reset',
)


def _looks_like_offline(err: Exception) -> bool:
  """True if the error message plausibly means 'adb lost the device'."""
  # We concatenate every channel that might carry the adb daemon's verbatim
  # stdout/stderr: str(err), repr(err), and (for CalledProcessError) the
  # `output` / `stderr` attributes. Cheap and robust across adb/adb_env
  # version drift.
  parts = [str(err), repr(err)]
  for attr in ('output', 'stderr', 'stdout'):
    raw = getattr(err, attr, None)
    if raw is None:
      continue
    if isinstance(raw, bytes):
      parts.append(raw.decode('utf-8', 'replace'))
    else:
      parts.append(str(raw))
  msg = ' '.join(parts).lower().encode('utf-8', 'replace')
  return any(marker in msg for marker in _OFFLINE_MARKERS)


def _build_resilient_exec(underlying, on_offline):
  """Wraps ``underlying(cmd, **kw)`` to auto-recover only on offline-like
  adb failures.

  Args:
    underlying: The original ``adb_controller.execute_command`` callable.
    on_offline: Zero-arg recovery hook (e.g. `_hard_reset_adb`). Invoked
      exactly once per wrapped call, before retrying.

  Returns:
    A callable with the same signature as ``underlying``. On non-offline
    failures the original exception is re-raised untouched so callers can
    see (and tests can assert on) the real error.
  """
  def _resilient_exec(command, **kwargs):
    try:
      return underlying(command, **kwargs)
    except (adb_errors.AdbControllerError,
            subprocess.CalledProcessError) as e:
      if not _looks_like_offline(e):
        raise
      logging.warning(
          'ADB command %s looks like device-offline (%s); '
          'hard-resetting adb server and retrying once.',
          command,
          e,
      )
      on_offline()
      return underlying(command, **kwargs)
  return _resilient_exec


class RedroidEnv(env_interface.AndroidEnvInterface):
  """Minimal AndroidEnv implementation backed by direct ADB calls."""

  def __init__(
      self,
      adb_path: str,
      device_name: str,
      adb_server_port: int = 5037,
  ):
    self._adb_config = config_classes.AdbControllerConfig(
        adb_path=adb_path,
        adb_server_port=adb_server_port,
        device_name=device_name,
    )
    self._adb_controller = adb_controller.AdbController(self._adb_config)
    self._adb_controller.init_server()
    # Monkey-patch execute_command so that ADB calls auto-reconnect when
    # (and only when) the local adb server has silently dropped the
    # TCP-forwarded device. Other errors are propagated untouched so that
    # legitimate command failures (e.g. "unable to open database file" for
    # a non-existent provider DB) are not masked by a spurious reconnect.
    self._adb_controller.execute_command = _build_resilient_exec(
        self._adb_controller.execute_command,
        on_offline=self._hard_reset_adb,
    )
    self._adb_parser = adb_call_parser.AdbCallParser(self._adb_controller)
    self._raw_action: dict[str, np.ndarray] | None = None
    # Ensure device is connected before first screenshot.
    self._ensure_connected()
    self._raw_observation: dict[str, np.ndarray] = {
        'pixels': self._capture_screenshot(),
    }

  def _ensure_connected(self) -> None:
    """Runs ``adb connect`` to keep the TCP-forwarded device reachable.

    TCP-transported ADB connections (e.g. via SSH port-forwarding) are
    silently dropped by the local adb server after a period of inactivity.
    Calling ``adb connect`` is cheap and idempotent — it either prints
    ``already connected`` or re-establishes the transport.
    """
    device = self._adb_config.device_name
    adb = self._adb_config.adb_path
    port = self._adb_config.adb_server_port
    try:
      subprocess.check_output(
          [adb, '-P', str(port), 'connect', device],
          timeout=10,
          stderr=subprocess.STDOUT,
      )
    except Exception as e:  # pylint: disable=broad-except
      logging.warning('adb connect %s failed: %s', device, e)

  def _hard_reset_adb(self) -> None:
    """Full adb daemon bounce + reconnect. Used when a plain ``adb connect``
    is not enough to recover (e.g. after a transient SSH-tunnel hiccup left
    the local adbd in a bad state and subsequent commands return
    ``device 'X' not found``)."""
    adb = self._adb_config.adb_path
    port = self._adb_config.adb_server_port
    for args in (['kill-server'], ['start-server']):
      try:
        subprocess.check_output(
            [adb, '-P', str(port), *args],
            timeout=10,
            stderr=subprocess.STDOUT,
        )
      except Exception as e:  # pylint: disable=broad-except
        logging.warning('adb %s failed: %s', ' '.join(args), e)
    self._ensure_connected()

  def action_spec(self) -> dict[str, specs.Array]:
    return {
        'action_type': specs.Array(shape=(), dtype=np.int32),
        'touch_position': specs.Array(shape=(2,), dtype=np.float32),
    }

  def observation_spec(self) -> dict[str, specs.Array]:
    pixels = self._raw_observation['pixels']
    return {
        'pixels': specs.Array(shape=pixels.shape, dtype=pixels.dtype),
    }

  def reset(self) -> dm_env.TimeStep:
    self._raw_action = None
    self._ensure_connected()
    self._raw_observation = {'pixels': self._capture_screenshot()}
    return dm_env.restart(self._raw_observation)

  def step(self, action: dict[str, np.ndarray]) -> dm_env.TimeStep:
    self._raw_action = action
    self._raw_observation = {'pixels': self._capture_screenshot()}
    return dm_env.transition(
        reward=0.0,
        observation=self._raw_observation,
        discount=1.0,
    )

  def close(self) -> None:
    logging.info('Closing RedroidEnv for %s', self._adb_config.device_name)

  @property
  def raw_action(self) -> dict[str, np.ndarray] | None:
    return self._raw_action

  @property
  def raw_observation(self) -> dict[str, np.ndarray]:
    return self._raw_observation

  def stats(self) -> dict[str, Any]:
    return {'adb_server_port': float(self._adb_config.adb_server_port)}

  def execute_adb_call(self, call: adb_pb2.AdbRequest) -> adb_pb2.AdbResponse:
    return self._adb_parser.parse(call)

  def _capture_screenshot(self) -> np.ndarray:
    output = self._adb_controller.execute_command(
        ['exec-out', 'screencap', '-p']
    )
    image = cv2.imdecode(
        np.frombuffer(output, dtype=np.uint8), cv2.IMREAD_COLOR
    )
    if image is None:
      raise ValueError('Failed to decode screenshot from redroid.')
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
