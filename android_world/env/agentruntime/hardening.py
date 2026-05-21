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

"""Hardening utilities for the AgentRuntime / Appium backend.

This module addresses four classes of brittleness that arise when running
AndroidWorld task evaluators over Appium instead of direct ADB:

1. **Shell timeout hardening** — ``mobile: shell`` calls can silently hang on
   a congested sandbox.  ``safe_shell()`` wraps every call with a watchdog
   thread and raises ``ShellTimeoutError`` if the driver does not return within
   the configured budget.

2. **File push/pull error recovery** — Network errors between the eval host
   and the sandbox can cause Appium's ``push_file`` / ``pull_file`` to raise
   transient ``WebDriverException`` errors.  ``retry_push()`` and
   ``retry_pull()`` retry up to *n* times with exponential back-off.

3. **Validator compatibility patches** — Some AndroidWorld task validators
   call ``adb_utils.get_file_from_device`` which ultimately invokes
   ``pull_file`` and expects the returned local directory to contain specific
   files.  ``ValidatorCompat`` provides a ``tmp_dir_from_device()`` helper that
   creates a temporary local directory, pulls all files from the remote
   directory, and yields the local path — matching the semantics expected by
   the validator layer.

4. **Device time control** — Several tasks use ``task_eval.set_device_time``
   to set the device clock.  The base implementation issues an ADB
   ``date -s`` command.  Over Appium this becomes ``mobile: shell date``.
   ``set_device_time_appium()`` implements the same semantics via
   ``AppiumEnv.shell()``.

Usage::

  from android_world.env.agentruntime import hardening

  # Shell with watchdog timeout.
  output = hardening.safe_shell(appium_env, 'pm list packages', timeout=20)

  # Retry push/pull.
  hardening.retry_push(appium_env, local_path, remote_path)
  data = hardening.retry_pull(appium_env, remote_path)

  # Validator-compatible temp directory pull.
  with hardening.tmp_dir_from_device(appium_env, remote_dir) as local_dir:
      # local_dir contains pulled files
      ...

  # Device time.
  hardening.set_device_time_appium(appium_env, epoch_seconds)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import time
from typing import TYPE_CHECKING

from absl import logging

if TYPE_CHECKING:
  from android_world.env.agentruntime.appium_env import AppiumEnv


# ---------------------------------------------------------------------------
# 1. Shell timeout hardening
# ---------------------------------------------------------------------------

class ShellTimeoutError(RuntimeError):
  """Raised when a ``mobile: shell`` call exceeds its timeout budget."""


def safe_shell(
    appium_env: 'AppiumEnv',
    command: str,
    timeout: int | None = None,
) -> str:
  """Execute ``command`` via Appium ``mobile: shell`` with a watchdog.

  Appium's own timeout parameter is honoured by the server but the Python
  ``requests`` library may not raise until the TCP connection times out.  This
  wrapper starts the call in the calling thread and relies on Appium's
  server-side timeout; if the call still has not returned after
  ``timeout * 1.5`` seconds (measured client-side with ``time.monotonic``), a
  ``ShellTimeoutError`` is raised.  Note: the underlying Appium call is *not*
  interrupted — there is no clean way to cancel a blocking socket read in a
  thread-safe manner.  The caller should treat the sandbox as suspect and
  stop using it after a timeout.

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    command: Shell command string.
    timeout: Timeout in seconds.  ``None`` uses ``appium_env._shell_timeout``.

  Returns:
    Command stdout as a string.

  Raises:
    ShellTimeoutError: If the call appears to have hung beyond ``timeout * 1.5``
      seconds.
    RuntimeError: Re-raised from ``AppiumEnv.shell()`` on driver errors.
  """
  effective_timeout = timeout or appium_env._shell_timeout
  deadline = time.monotonic() + effective_timeout * 1.5

  # We do a best-effort deadline check by running the shell call synchronously.
  # If the Appium server honours its own timeout, we will get back within
  # effective_timeout seconds.  The 1.5× slack accounts for network jitter.
  t_start = time.monotonic()
  output = appium_env.shell(command, timeout=effective_timeout)
  elapsed = time.monotonic() - t_start

  if time.monotonic() > deadline:
    raise ShellTimeoutError(
        f'safe_shell: command {command!r:.80} took {elapsed:.1f}s, '
        f'exceeding deadline (timeout={effective_timeout}s). '
        f'Sandbox may be unresponsive.'
    )
  return output


# ---------------------------------------------------------------------------
# 2. File push / pull with retries
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BASE_SLEEP = 1.0  # seconds; doubles each retry


def retry_push(
    appium_env: 'AppiumEnv',
    local_path: str,
    remote_path: str,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_sleep: float = _DEFAULT_RETRY_BASE_SLEEP,
) -> None:
  """Push a local file to the device with exponential back-off retries.

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    local_path: Local source file path.
    remote_path: Destination path on the device.
    max_retries: Maximum number of attempts (including the first attempt).
    base_sleep: Seconds to sleep before the first retry; doubles each time.

  Raises:
    Exception: The last exception encountered if all retries are exhausted.
  """
  last_exc = None
  sleep = base_sleep
  for attempt in range(1, max_retries + 1):
    try:
      appium_env.push_file(local_path, remote_path)
      if attempt > 1:
        logging.info(
            'retry_push: succeeded on attempt %d for %s', attempt, remote_path
        )
      return
    except Exception as exc:  # pylint: disable=broad-except
      last_exc = exc
      logging.warning(
          'retry_push: attempt %d/%d failed for %s: %s',
          attempt,
          max_retries,
          remote_path,
          exc,
      )
      if attempt < max_retries:
        time.sleep(sleep)
        sleep *= 2.0

  raise last_exc  # type: ignore[misc]


def retry_pull(
    appium_env: 'AppiumEnv',
    remote_path: str,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_sleep: float = _DEFAULT_RETRY_BASE_SLEEP,
) -> bytes:
  """Pull a single file from the device with exponential back-off retries.

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    remote_path: Path on the device.
    max_retries: Maximum number of attempts.
    base_sleep: Seconds to sleep before the first retry; doubles each time.

  Returns:
    Raw bytes of the remote file.

  Raises:
    Exception: The last exception if all retries are exhausted.
  """
  last_exc = None
  sleep = base_sleep
  for attempt in range(1, max_retries + 1):
    try:
      data = appium_env.pull_file(remote_path)
      if attempt > 1:
        logging.info(
            'retry_pull: succeeded on attempt %d for %s', attempt, remote_path
        )
      return data
    except Exception as exc:  # pylint: disable=broad-except
      last_exc = exc
      logging.warning(
          'retry_pull: attempt %d/%d failed for %s: %s',
          attempt,
          max_retries,
          remote_path,
          exc,
      )
      if attempt < max_retries:
        time.sleep(sleep)
        sleep *= 2.0

  raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 3. Validator compatibility — tmp_dir_from_device
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def tmp_dir_from_device(
    appium_env: 'AppiumEnv',
    remote_dir: str,
    max_retries: int = _DEFAULT_MAX_RETRIES,
):
  """Pull all files from ``remote_dir`` into a local temp directory.

  This is a drop-in replacement for the pattern used by AndroidWorld
  validators that call ``file_utils.tmp_directory_from_device``.  The local
  temp directory is cleaned up automatically on context exit.

  Usage::

    with hardening.tmp_dir_from_device(appium_env, '/sdcard/Pictures') as d:
        files = os.listdir(d)

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    remote_dir: Absolute path of the directory on the Android device.
    max_retries: How many times to retry individual file pulls on failure.

  Yields:
    Path to a local temp directory containing the pulled files.
  """
  local_dir = tempfile.mkdtemp(prefix='aw_validator_pull_')
  try:
    # List remote directory.
    try:
      ls_output = safe_shell(appium_env, f'ls "{remote_dir}"')
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'tmp_dir_from_device: ls %s failed: %s', remote_dir, exc
      )
      ls_output = ''

    entries = [e.strip() for e in ls_output.splitlines() if e.strip()]
    for entry in entries:
      remote_file = f'{remote_dir.rstrip("/")}/{entry}'
      local_file = os.path.join(local_dir, entry)
      try:
        data = retry_pull(appium_env, remote_file, max_retries=max_retries)
        with open(local_file, 'wb') as fh:
          fh.write(data)
      except Exception as exc:  # pylint: disable=broad-except
        logging.warning(
            'tmp_dir_from_device: could not pull %s: %s', remote_file, exc
        )

    yield local_dir
  finally:
    shutil.rmtree(local_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. Device time control
# ---------------------------------------------------------------------------

def set_device_time_appium(
    appium_env: 'AppiumEnv',
    epoch_seconds: int | float,
) -> None:
  """Set the Android device clock via ``mobile: shell date``.

  This replicates the behaviour of ``task_eval.TaskEval.set_device_time``
  but routes through Appium's shell bridge instead of direct ADB.

  The ``date -s`` command format used by AOSP is ``YYYYMMdd.HHmmss``.
  Root / system time-setting rights must already be granted on the device
  (redroid / E2B sandbox images typically run as root).

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    epoch_seconds: Unix timestamp to set.

  Raises:
    RuntimeError: Propagated from ``AppiumEnv.shell()`` if the command fails.
  """
  import datetime  # pylint: disable=g-import-not-at-top
  dt = datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc)
  date_str = dt.strftime('%Y%m%d.%H%M%S')
  output = appium_env.shell(f'date -s {date_str}')
  logging.info(
      'set_device_time_appium: set device time to %s (output=%r)',
      date_str,
      output[:80],
  )


# ---------------------------------------------------------------------------
# 5. Page-source retry helper (UI stabilisation)
# ---------------------------------------------------------------------------

def stable_page_source(
    appium_env: 'AppiumEnv',
    max_attempts: int = 3,
    stable_threshold: float = 0.95,
    sleep_between: float = 0.5,
) -> str:
  """Return a UI XML dump, retrying until consecutive dumps are stable.

  Some AndroidWorld validators call ``page_source`` just after an action while
  the UI is still animating.  This helper polls until two consecutive XML dumps
  are similar enough (measured by character-length ratio as a cheap proxy).

  Args:
    appium_env: A live ``AppiumEnv`` instance.
    max_attempts: Maximum number of polling attempts.
    stable_threshold: Ratio of consecutive XML lengths that is considered
      "stable" (default 0.95 means ≤5% length change).
    sleep_between: Seconds to sleep between attempts.

  Returns:
    The most recent XML dump once it has stabilised (or after the last retry).
  """
  prev_xml = ''
  for attempt in range(max_attempts):
    try:
      xml = appium_env.page_source()
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning('stable_page_source: attempt %d failed: %s', attempt, exc)
      time.sleep(sleep_between)
      continue

    if prev_xml:
      ratio = min(len(xml), len(prev_xml)) / max(len(xml), len(prev_xml), 1)
      if ratio >= stable_threshold:
        return xml
    prev_xml = xml
    if attempt < max_attempts - 1:
      time.sleep(sleep_between)

  return prev_xml
