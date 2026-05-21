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

"""Unit tests for hardening utilities."""

import base64
import os
import tempfile
from unittest import mock

from absl.testing import absltest
from android_world.env.agentruntime import appium_env as appium_env_lib
from android_world.env.agentruntime import hardening


def _make_env():
  env = mock.create_autospec(appium_env_lib.AppiumEnv, instance=True)
  env._shell_timeout = 30
  env.shell.return_value = ''
  return env


class SafeShellTest(absltest.TestCase):

  def test_returns_output_on_success(self):
    env = _make_env()
    env.shell.return_value = 'package:android'
    result = hardening.safe_shell(env, 'pm list packages android', timeout=10)
    self.assertEqual(result, 'package:android')
    env.shell.assert_called_once_with('pm list packages android', timeout=10)

  def test_propagates_runtime_error(self):
    env = _make_env()
    env.shell.side_effect = RuntimeError('mobile: shell failed')
    with self.assertRaises(RuntimeError):
      hardening.safe_shell(env, 'bad cmd', timeout=5)


class RetryPushTest(absltest.TestCase):

  def test_succeeds_on_first_try(self):
    env = _make_env()
    hardening.retry_push(env, '/local/file.db', '/remote/file.db')
    env.push_file.assert_called_once_with('/local/file.db', '/remote/file.db')

  def test_retries_on_transient_failure(self):
    env = _make_env()
    env.push_file.side_effect = [Exception('network error'), None]
    with mock.patch('time.sleep'):
      hardening.retry_push(
          env, '/local/file.db', '/remote/file.db',
          max_retries=2, base_sleep=0.0
      )
    self.assertEqual(env.push_file.call_count, 2)

  def test_raises_after_max_retries(self):
    env = _make_env()
    env.push_file.side_effect = Exception('permanent failure')
    with mock.patch('time.sleep'):
      with self.assertRaises(Exception) as ctx:
        hardening.retry_push(
            env, '/local/file.db', '/remote/file.db',
            max_retries=3, base_sleep=0.0
        )
    self.assertIn('permanent failure', str(ctx.exception))
    self.assertEqual(env.push_file.call_count, 3)


class RetryPullTest(absltest.TestCase):

  def test_succeeds_and_returns_bytes(self):
    env = _make_env()
    env.pull_file.return_value = b'\x00\x01\x02'
    result = hardening.retry_pull(env, '/remote/file.bin')
    self.assertEqual(result, b'\x00\x01\x02')

  def test_retries_on_error(self):
    env = _make_env()
    env.pull_file.side_effect = [Exception('network error'), b'ok']
    with mock.patch('time.sleep'):
      result = hardening.retry_pull(
          env, '/remote/file.bin', max_retries=2, base_sleep=0.0
      )
    self.assertEqual(result, b'ok')


class TmpDirFromDeviceTest(absltest.TestCase):

  def test_pulls_files_to_local_dir(self):
    env = _make_env()
    env.shell.return_value = 'contacts.db\ncontacts.db-wal\n'
    env.pull_file.return_value = b'db_content'
    local_files = []
    with hardening.tmp_dir_from_device(env, '/data/user/0/com.android.providers.contacts/databases') as d:
      local_files = os.listdir(d)
    self.assertIn('contacts.db', local_files)
    self.assertIn('contacts.db-wal', local_files)

  def test_cleans_up_local_dir_after_exit(self):
    env = _make_env()
    env.shell.return_value = ''
    captured_path = []
    with hardening.tmp_dir_from_device(env, '/sdcard') as d:
      captured_path.append(d)
    self.assertFalse(os.path.exists(captured_path[0]))

  def test_handles_ls_failure_gracefully(self):
    env = _make_env()
    env.shell.side_effect = RuntimeError('ls failed')
    # Should not raise; just yield an empty directory.
    with hardening.tmp_dir_from_device(env, '/missing') as d:
      self.assertEqual(os.listdir(d), [])


class SetDeviceTimeAppiumTest(absltest.TestCase):

  def test_calls_shell_with_date_format(self):
    env = _make_env()
    env.shell.return_value = ''
    # Unix epoch 0 → 1970-01-01 00:00:00 UTC → date string 19700101.000000
    hardening.set_device_time_appium(env, 0)
    env.shell.assert_called_once()
    cmd = env.shell.call_args[0][0]
    self.assertIn('date -s', cmd)
    self.assertIn('19700101.000000', cmd)


class StablePageSourceTest(absltest.TestCase):

  def test_returns_xml_when_stable(self):
    env = _make_env()
    xml = '<hierarchy><node/></hierarchy>'
    env.page_source.return_value = xml
    with mock.patch('time.sleep'):
      result = hardening.stable_page_source(env, max_attempts=3)
    self.assertEqual(result, xml)

  def test_returns_last_xml_after_max_attempts(self):
    env = _make_env()
    # Return increasingly different XML each call.
    env.page_source.side_effect = [
        '<a/>',
        '<a><b/></a>',
        '<a><b/><c/></a>',
    ]
    with mock.patch('time.sleep'):
      result = hardening.stable_page_source(env, max_attempts=3)
    self.assertIsInstance(result, str)

  def test_handles_page_source_exception(self):
    env = _make_env()
    env.page_source.side_effect = [Exception('driver error'), '<hierarchy/>']
    with mock.patch('time.sleep'):
      result = hardening.stable_page_source(env, max_attempts=3)
    self.assertIsNotNone(result)


if __name__ == '__main__':
  absltest.main()
