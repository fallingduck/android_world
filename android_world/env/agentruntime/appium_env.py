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

"""Single-sandbox Appium environment for the AgentRuntime backend.

``AppiumEnv`` owns a single Appium WebDriver session and exposes the low-level
device capabilities used by ``AppiumAdbBridge`` and ``AppiumController``:

  - screenshot (PNG bytes → numpy RGB array)
  - page_source / UI XML dump
  - mobile: shell execution
  - file push / pull  (single-file and directory-level)
  - app lifecycle  (start, stop, current activity, window size)
  - quit / cleanup

Design notes:
  - All methods are synchronous / blocking.  The caller is responsible for
    running them in a ThreadPoolExecutor if concurrency is needed.
  - ``mobile: shell`` is the workhorse for all ADB-semantic operations.
    We set a generous default timeout (``shell_timeout``) because some
    commands (e.g. ``uiautomator dump``) can take several seconds on a
    loaded sandbox.
  - ``pull_directory`` lists the remote directory via ``mobile: shell ls``
    then iterates Appium ``pull_file`` calls.  This is the correct semantic
    to match ``AndroidWorldController.pull_file`` which operates on a whole
    directory, not a single file.
"""

from __future__ import annotations

import base64
import os
import tempfile
from typing import TYPE_CHECKING, Optional

from absl import logging
import numpy as np

if TYPE_CHECKING:
  pass  # Appium driver typed as object to avoid hard import


class AppiumEnv:
  """Wraps an Appium WebDriver session with AndroidWorld-friendly helpers.

  Args:
    driver: A connected ``appium.webdriver.Remote`` instance.
    shell_timeout: Default timeout in seconds for ``mobile: shell`` calls.
    sandbox_id: Optional sandbox identifier used in log messages.
  """

  def __init__(
      self,
      driver,  # appium.webdriver.Remote
      shell_timeout: int = 30,
      sandbox_id: str = '',
  ):
    self._driver = driver
    self._shell_timeout = shell_timeout
    self._sandbox_id = sandbox_id

  # ------------------------------------------------------------------
  # Core observation primitives
  # ------------------------------------------------------------------

  def screenshot(self) -> np.ndarray:
    """Return a screenshot as an RGB numpy array (H×W×3, uint8).

    Returns:
      numpy array of shape (height, width, 3) in RGB order.
    """
    import base64  # pylint: disable=g-import-not-at-top
    import io  # pylint: disable=g-import-not-at-top
    from PIL import Image  # pylint: disable=g-import-not-at-top

    # Use base64 screenshot to avoid cv2 decode issues with certain Appium
    # backends (e.g. AGS sandbox) where get_screenshot_as_png() may return
    # data that cv2.imdecode cannot handle.
    b64 = self._driver.get_screenshot_as_base64()
    png_bytes = base64.b64decode(b64)
    img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
    return np.array(img)

  def page_source(self) -> str:
    """Return the current UI XML (uiautomator dump) as a string."""
    return self._driver.page_source

  def window_size(self) -> tuple[int, int]:
    """Return the logical screen size as ``(width, height)`` in pixels."""
    size = self._driver.get_window_size()
    return int(size['width']), int(size['height'])

  # ------------------------------------------------------------------
  # Shell execution
  # ------------------------------------------------------------------

  def shell(
      self,
      command: str,
      timeout: Optional[int] = None,
  ) -> str:
    """Execute a shell command on the device via ``mobile: shell``.

    Args:
      command: The shell command string, e.g. ``'pm list packages'``.
      timeout: Timeout in seconds.  Defaults to ``self._shell_timeout``.

    Returns:
      The combined stdout of the command as a string.

    Raises:
      RuntimeError: If Appium raises an exception executing the command.
    """
    timeout_ms = (timeout or self._shell_timeout) * 1000
    try:
      result = self._driver.execute_script(
          'mobile: shell',
          {
              'command': command,
              'timeout': timeout_ms,
              'includeStderr': True,
          },
      )
      # Appium may return a dict {'stdout': ..., 'stderr': ...} or a plain str.
      if isinstance(result, dict):
        stdout = result.get('stdout', '') or ''
        stderr = result.get('stderr', '') or ''
        if stderr:
          logging.debug(
              'shell stderr (sandbox=%s, cmd=%r): %s',
              self._sandbox_id,
              command[:80],
              stderr,
          )
        return stdout
      return str(result) if result is not None else ''
    except Exception as exc:
      raise RuntimeError(
          f'mobile: shell failed (sandbox={self._sandbox_id!r},'
          f' cmd={command!r}): {exc}'
      ) from exc

  # ------------------------------------------------------------------
  # File operations
  # ------------------------------------------------------------------

  def push_file(self, local_path: str, remote_path: str) -> None:
    """Push a local file to the device.

    Args:
      local_path: Absolute path of the file on the local machine.
      remote_path: Absolute path of the destination on the Android device.
    """
    with open(local_path, 'rb') as f:
      content_b64 = base64.b64encode(f.read()).decode('ascii')
    self._driver.push_file(remote_path, content_b64)
    logging.debug(
        'Pushed file to device: %s -> %s (sandbox=%s)',
        local_path,
        remote_path,
        self._sandbox_id,
    )

  def pull_file(self, remote_path: str) -> bytes:
    """Pull a single file from the device.

    Args:
      remote_path: Absolute path of the file on the Android device.

    Returns:
      Raw bytes of the file.
    """
    b64_content = self._driver.pull_file(remote_path)
    return base64.b64decode(b64_content)

  def pull_directory(
      self,
      remote_dir: str,
      local_dir: str,
  ) -> list[str]:
    """Pull all files from a remote directory to a local directory.

    This implements the directory-level semantics expected by
    ``AndroidWorldController.pull_file``: the caller passes a remote *file*
    path, the controller takes ``dirname`` of it, and expects all sibling
    files to be available in the returned local directory.

    Args:
      remote_dir: Absolute path of the directory on the Android device.
      local_dir: Local directory to write pulled files into (must exist).

    Returns:
      List of local file paths that were successfully pulled.

    Notes:
      - Uses ``mobile: shell ls`` to enumerate the remote directory.
      - Silently skips entries that fail to pull (e.g. sub-directories,
        sockets, or permission-denied files) and logs a warning.
    """
    # List the remote directory.
    ls_output = self.shell(f'ls "{remote_dir}"')
    entries = [e.strip() for e in ls_output.splitlines() if e.strip()]

    pulled: list[str] = []
    for entry in entries:
      remote_file = f'{remote_dir.rstrip("/")}/{entry}'
      local_file = os.path.join(local_dir, entry)
      try:
        data = self.pull_file(remote_file)
        with open(local_file, 'wb') as fh:
          fh.write(data)
        pulled.append(local_file)
      except Exception as exc:  # pylint: disable=broad-except
        logging.warning(
            'Could not pull %s (sandbox=%s): %s',
            remote_file,
            self._sandbox_id,
            exc,
        )
    return pulled

  # ------------------------------------------------------------------
  # App lifecycle
  # ------------------------------------------------------------------

  def current_activity(self) -> tuple[str, str]:
    """Return ``(package, activity)`` of the foreground activity.

    Uses ``dumpsys activity activities`` via shell to reliably obtain the
    current activity, falling back to Appium's ``current_activity`` and
    ``current_package`` properties if the shell approach fails.

    Returns:
      A ``(package_name, activity_name)`` tuple.  Both may be empty strings
      if the activity cannot be determined.
    """
    try:
      output = self.shell(
          'dumpsys activity activities | grep "mResumedActivity"'
      )
      # Expected format: "mResumedActivity: ActivityRecord{... pkg/activity ...}"
      for line in output.splitlines():
        line = line.strip()
        if 'mResumedActivity' in line or 'ResumedActivity' in line:
          # Extract the "package/activity" token.
          parts = line.split()
          for part in parts:
            if '/' in part and not part.startswith('{') and not part.startswith('}'):
              pkg, _, act = part.partition('/')
              return pkg.strip(), act.strip()
    except Exception as exc:  # pylint: disable=broad-except
      logging.debug(
          'dumpsys activity failed (sandbox=%s): %s', self._sandbox_id, exc
      )

    # Fallback to Appium's built-in properties.
    try:
      pkg = self._driver.current_package or ''
      act = self._driver.current_activity or ''
      return pkg, act
    except Exception:  # pylint: disable=broad-except
      return '', ''

  def activate_app(self, package_name: str) -> None:
    """Bring an app to the foreground."""
    self._driver.activate_app(package_name)

  def terminate_app(self, package_name: str) -> bool:
    """Terminate an app.  Returns True if the app was running."""
    return bool(self._driver.terminate_app(package_name))

  def query_app_state(self, package_name: str) -> int:
    """Return the Appium app state integer (0=not installed, 4=running)."""
    from appium.webdriver.common.appiumby import AppiumBy  # noqa: F401
    return self._driver.query_app_state(package_name)

  # ------------------------------------------------------------------
  # Orientation
  # ------------------------------------------------------------------

  def get_orientation(self) -> str:
    """Return current screen orientation as ``'PORTRAIT'`` or ``'LANDSCAPE'``."""
    return self._driver.orientation

  def set_orientation(self, orientation: str) -> None:
    """Set screen orientation.  ``orientation`` must be ``'PORTRAIT'`` or
    ``'LANDSCAPE'``."""
    self._driver.orientation = orientation.upper()

  # ------------------------------------------------------------------
  # Logcat
  # ------------------------------------------------------------------

  def dump_logcat(self, lines: int = 500) -> str:
    """Return the last ``lines`` lines of device logcat."""
    try:
      return self.shell(f'logcat -d -t {lines}')
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'logcat dump failed (sandbox=%s): %s', self._sandbox_id, exc
      )
      return ''

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  @property
  def driver(self):
    """Expose the raw Appium WebDriver (use sparingly)."""
    return self._driver

  def quit(self) -> None:
    """Quit the Appium WebDriver session (does NOT kill the sandbox)."""
    try:
      self._driver.quit()
      logging.info('Appium driver session closed (sandbox=%s).', self._sandbox_id)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'Appium driver quit raised an exception (sandbox=%s): %s',
          self._sandbox_id,
          exc,
      )

  def __repr__(self) -> str:
    return f'AppiumEnv(sandbox={self._sandbox_id!r})'
