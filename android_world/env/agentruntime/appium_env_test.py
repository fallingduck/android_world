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

"""Unit tests for AppiumEnv."""

import base64
import os
import tempfile
from unittest import mock

import numpy as np
from absl.testing import absltest
from android_world.env.agentruntime import appium_env as appium_env_lib


def _make_env(driver=None):
  if driver is None:
    driver = mock.MagicMock()
  return appium_env_lib.AppiumEnv(
      driver=driver, shell_timeout=10, sandbox_id='test-sb'
  )


class AppiumEnvScreenshotTest(absltest.TestCase):

  def test_screenshot_returns_rgb_array(self):
    """screenshot() converts PNG bytes to an RGB numpy array."""
    import cv2  # pylint: disable=g-import-not-at-top
    # Build a tiny 2×2 RGB image and encode as PNG.
    img = np.array([[[255, 0, 0], [0, 255, 0]],
                    [[0, 0, 255], [255, 255, 0]]], dtype=np.uint8)
    _, buf = cv2.imencode('.png', cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    png_bytes = buf.tobytes()

    driver = mock.MagicMock()
    driver.get_screenshot_as_png.return_value = png_bytes
    env = _make_env(driver)

    result = env.screenshot()
    self.assertIsInstance(result, np.ndarray)
    self.assertEqual(result.shape[2], 3)
    self.assertEqual(result.dtype, np.uint8)

  def test_screenshot_raises_on_invalid_bytes(self):
    driver = mock.MagicMock()
    driver.get_screenshot_as_png.return_value = b'not_a_png'
    env = _make_env(driver)
    with self.assertRaises(ValueError):
      env.screenshot()


class AppiumEnvPageSourceTest(absltest.TestCase):

  def test_page_source_returns_driver_value(self):
    driver = mock.MagicMock()
    driver.page_source = '<hierarchy><node/></hierarchy>'
    env = _make_env(driver)
    self.assertEqual(env.page_source(), '<hierarchy><node/></hierarchy>')


class AppiumEnvShellTest(absltest.TestCase):

  def test_shell_returns_stdout_from_dict(self):
    driver = mock.MagicMock()
    driver.execute_script.return_value = {
        'stdout': 'package:android\n', 'stderr': ''
    }
    env = _make_env(driver)
    out = env.shell('pm list packages android')
    self.assertEqual(out, 'package:android\n')
    driver.execute_script.assert_called_once()
    call_args = driver.execute_script.call_args
    self.assertEqual(call_args[0][0], 'mobile: shell')

  def test_shell_returns_plain_string(self):
    driver = mock.MagicMock()
    driver.execute_script.return_value = 'hello'
    env = _make_env(driver)
    self.assertEqual(env.shell('echo hello'), 'hello')

  def test_shell_raises_runtime_error_on_driver_exc(self):
    driver = mock.MagicMock()
    driver.execute_script.side_effect = Exception('conn reset')
    env = _make_env(driver)
    with self.assertRaises(RuntimeError) as ctx:
      env.shell('pm list packages')
    self.assertIn('mobile: shell failed', str(ctx.exception))

  def test_shell_uses_custom_timeout(self):
    driver = mock.MagicMock()
    driver.execute_script.return_value = ''
    env = _make_env(driver)
    env.shell('ls /sdcard', timeout=60)
    call_args = driver.execute_script.call_args
    params = call_args[0][1]
    self.assertEqual(params['timeout'], 60_000)


class AppiumEnvFileOpsTest(absltest.TestCase):

  def test_push_file_encodes_as_base64(self):
    driver = mock.MagicMock()
    env = _make_env(driver)
    payload = b'hello world'
    with tempfile.NamedTemporaryFile(delete=False) as f:
      f.write(payload)
      tmp = f.name
    try:
      env.push_file(tmp, '/sdcard/test.txt')
    finally:
      os.unlink(tmp)
    driver.push_file.assert_called_once()
    remote, b64 = driver.push_file.call_args[0]
    self.assertEqual(remote, '/sdcard/test.txt')
    self.assertEqual(base64.b64decode(b64), payload)

  def test_pull_file_decodes_base64(self):
    driver = mock.MagicMock()
    payload = b'\x00\x01\x02\xff'
    driver.pull_file.return_value = base64.b64encode(payload).decode()
    env = _make_env(driver)
    result = env.pull_file('/sdcard/test.bin')
    self.assertEqual(result, payload)

  def test_pull_directory_skips_failed_entries(self):
    driver = mock.MagicMock()
    driver.execute_script.return_value = {'stdout': 'a.db\nb.db\n', 'stderr': ''}
    # First pull succeeds, second raises.
    driver.pull_file.side_effect = [
        base64.b64encode(b'data_a'),
        Exception('pull failed'),
    ]
    env = _make_env(driver)
    with tempfile.TemporaryDirectory() as local_dir:
      pulled = env.pull_directory('/sdcard/db', local_dir)
    self.assertEqual(len(pulled), 1)


class AppiumEnvAppLifecycleTest(absltest.TestCase):

  def test_current_activity_parses_dumpsys(self):
    driver = mock.MagicMock()
    driver.execute_script.return_value = {
        'stdout': (
            '  mResumedActivity: ActivityRecord{abc}'
            ' com.example.app/com.example.app.MainActivity ...'
        ),
        'stderr': '',
    }
    env = _make_env(driver)
    pkg, act = env.current_activity()
    self.assertEqual(pkg, 'com.example.app')
    self.assertEqual(act, 'com.example.app.MainActivity')

  def test_current_activity_fallback_to_driver_props(self):
    driver = mock.MagicMock()
    # Make shell raise so we fall back to driver properties.
    driver.execute_script.side_effect = Exception('shell error')
    driver.current_package = 'com.fallback'
    driver.current_activity = '.FallbackActivity'
    env = _make_env(driver)
    pkg, act = env.current_activity()
    self.assertEqual(pkg, 'com.fallback')
    self.assertEqual(act, '.FallbackActivity')

  def test_quit_catches_exceptions(self):
    driver = mock.MagicMock()
    driver.quit.side_effect = Exception('already closed')
    env = _make_env(driver)
    env.quit()  # should not raise


if __name__ == '__main__':
  absltest.main()
