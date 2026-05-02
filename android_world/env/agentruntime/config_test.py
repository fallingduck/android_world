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

"""Unit tests for AgentRuntimeConfig."""

import os
import tempfile
from unittest import mock

from absl.testing import absltest
from android_world.env.agentruntime import config as config_lib


class AgentRuntimeConfigDefaultsTest(absltest.TestCase):

  def test_defaults(self):
    cfg = config_lib.AgentRuntimeConfig()
    self.assertIsNone(cfg.domain)
    self.assertIsNone(cfg.api_key)
    self.assertEqual(cfg.execution_mode, 'serial')
    self.assertEqual(cfg.sandbox_count, 1)
    self.assertEqual(cfg.thread_pool_size, 4)
    self.assertIsNone(cfg.sandbox_ids_file)

  def test_get_sandbox_ids_empty_by_default(self):
    cfg = config_lib.AgentRuntimeConfig()
    self.assertEqual(cfg.get_sandbox_ids(), [])

  def test_get_sandbox_ids_from_single_sandbox_id(self):
    cfg = config_lib.AgentRuntimeConfig(sandbox_id='sdx-abc')
    self.assertEqual(cfg.get_sandbox_ids(), ['sdx-abc'])


class AgentRuntimeConfigValidateTest(absltest.TestCase):

  def test_validate_missing_domain(self):
    cfg = config_lib.AgentRuntimeConfig(api_key='k', template='t')
    with self.assertRaises(ValueError) as ctx:
      cfg.validate()
    self.assertIn('domain', str(ctx.exception))

  def test_validate_missing_api_key(self):
    cfg = config_lib.AgentRuntimeConfig(domain='d', template='t')
    with self.assertRaises(ValueError) as ctx:
      cfg.validate()
    self.assertIn('api_key', str(ctx.exception))

  def test_validate_missing_sandbox_source(self):
    cfg = config_lib.AgentRuntimeConfig(domain='d', api_key='k')
    with self.assertRaises(ValueError) as ctx:
      cfg.validate()
    msg = str(ctx.exception).lower()
    self.assertTrue(
        'sandbox_id' in msg or 'template' in msg or 'sandbox_ids_file' in msg
    )

  def test_validate_ok_with_sandbox_id(self):
    cfg = config_lib.AgentRuntimeConfig(
        domain='d', api_key='k', sandbox_id='sb-123'
    )
    cfg.validate()  # should not raise

  def test_validate_ok_with_template(self):
    cfg = config_lib.AgentRuntimeConfig(
        domain='d', api_key='k', template='android_world_test'
    )
    cfg.validate()  # should not raise

  def test_validate_ok_with_sandbox_ids_file(self):
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.yaml', delete=False
    ) as f:
      f.write('sandbox_ids:\n  - sdx-abc\n')
      yaml_path = f.name
    try:
      cfg = config_lib.AgentRuntimeConfig(
          domain='d', api_key='k', sandbox_ids_file=yaml_path
      )
      cfg.validate()  # should not raise
    finally:
      os.unlink(yaml_path)

  def test_validate_invalid_execution_mode(self):
    cfg = config_lib.AgentRuntimeConfig(
        domain='d', api_key='k', template='t',
        execution_mode='batch',  # type: ignore[arg-type]
    )
    with self.assertRaises(ValueError) as ctx:
      cfg.validate()
    self.assertIn('execution_mode', str(ctx.exception))


class AgentRuntimeConfigFromEnvTest(absltest.TestCase):
  """Tests for from_env() using mobile-use variable names."""

  def test_from_env_mobile_use_names(self):
    """Primary variable names (E2B_* / SANDBOX_*) should be recognised."""
    env_vars = {
        'E2B_DOMAIN': 'ap-chongqing.tencentags.com',
        'E2B_API_KEY': 'ark_testkey',
        'SANDBOX_TEMPLATE': 'android_world_test',
        'SANDBOX_TIMEOUT': '600',
        'SANDBOX_COUNT': '5',
        'THREAD_POOL_SIZE': '8',
        'EXECUTION_MODE': 'parallel',
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
      cfg = config_lib.AgentRuntimeConfig.from_env()

    self.assertEqual(cfg.domain, 'ap-chongqing.tencentags.com')
    self.assertEqual(cfg.api_key, 'ark_testkey')
    self.assertEqual(cfg.template, 'android_world_test')
    self.assertEqual(cfg.timeout, 600)
    self.assertEqual(cfg.sandbox_count, 5)
    self.assertEqual(cfg.thread_pool_size, 8)
    self.assertEqual(cfg.execution_mode, 'parallel')

  def test_from_env_legacy_agentruntime_names(self):
    """Legacy AGENTRUNTIME_* names should still work as fallback."""
    env_vars = {
        'AGENTRUNTIME_DOMAIN': 'ap-guangzhou.tencentags.com',
        'AGENTRUNTIME_API_KEY': 'ark_legacy',
        'AGENTRUNTIME_TEMPLATE': 'mobile-v1',
        'AGENTRUNTIME_TIMEOUT': '300',
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
      # Ensure primary vars are NOT set so fallback is exercised.
      filtered = {k: v for k, v in os.environ.items()
                  if not k.startswith('E2B_') and k != 'SANDBOX_TEMPLATE'}
      with mock.patch.dict(os.environ, filtered, clear=True):
        with mock.patch.dict(os.environ, env_vars, clear=False):
          cfg = config_lib.AgentRuntimeConfig.from_env()

    self.assertEqual(cfg.domain, 'ap-guangzhou.tencentags.com')
    self.assertEqual(cfg.api_key, 'ark_legacy')

  def test_from_env_primary_overrides_legacy(self):
    """E2B_DOMAIN should win over AGENTRUNTIME_DOMAIN when both are set."""
    env_vars = {
        'E2B_DOMAIN': 'primary.tencentags.com',
        'AGENTRUNTIME_DOMAIN': 'legacy.tencentags.com',
        'E2B_API_KEY': 'key',
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
      cfg = config_lib.AgentRuntimeConfig.from_env()
    self.assertEqual(cfg.domain, 'primary.tencentags.com')

  def test_from_env_sandboxes_yaml(self):
    env_vars = {'SANDBOXES_YAML': '/path/to/sandboxes.yaml'}
    with mock.patch.dict(os.environ, env_vars, clear=False):
      cfg = config_lib.AgentRuntimeConfig.from_env()
    self.assertEqual(cfg.sandbox_ids_file, '/path/to/sandboxes.yaml')


class AgentRuntimeConfigFromSandboxesYamlTest(absltest.TestCase):

  def _write_yaml(self, content: str) -> str:
    f = tempfile.NamedTemporaryFile(
        mode='w', suffix='.yaml', delete=False
    )
    f.write(content)
    f.close()
    return f.name

  def test_loads_sandbox_ids(self):
    path = self._write_yaml('sandbox_ids:\n  - sdx-111\n  - sdx-222\n')
    try:
      cfg = config_lib.AgentRuntimeConfig.from_sandboxes_yaml(
          path, domain='d.com', api_key='k'
      )
      self.assertEqual(cfg.execution_mode, 'parallel')
      self.assertEqual(cfg.sandbox_count, 2)
      self.assertEqual(cfg.sandbox_ids_file, path)
      self.assertTrue(cfg.keep_sandbox)
      ids = cfg.get_sandbox_ids()
      self.assertEqual(ids, ['sdx-111', 'sdx-222'])
    finally:
      os.unlink(path)

  def test_raises_on_missing_file(self):
    with self.assertRaises(FileNotFoundError):
      config_lib.AgentRuntimeConfig.from_sandboxes_yaml('/nonexistent.yaml')

  def test_raises_on_empty_list(self):
    path = self._write_yaml('sandbox_ids: []\n')
    try:
      with self.assertRaises(ValueError):
        config_lib.AgentRuntimeConfig.from_sandboxes_yaml(path)
    finally:
      os.unlink(path)

  def test_raises_on_placeholder_values(self):
    path = self._write_yaml('sandbox_ids:\n  - xxx\n')
    try:
      with self.assertRaises(ValueError) as ctx:
        config_lib.AgentRuntimeConfig.from_sandboxes_yaml(path)
      self.assertIn('placeholder', str(ctx.exception))
    finally:
      os.unlink(path)

  def test_filters_placeholder_keeps_real(self):
    path = self._write_yaml('sandbox_ids:\n  - xxx\n  - sdx-real\n')
    try:
      cfg = config_lib.AgentRuntimeConfig.from_sandboxes_yaml(path)
      ids = cfg.get_sandbox_ids()
      self.assertEqual(ids, ['sdx-real'])
      self.assertEqual(cfg.sandbox_count, 1)
    finally:
      os.unlink(path)

  def test_missing_sandbox_ids_key(self):
    path = self._write_yaml('sandboxes:\n  - sdx-111\n')
    try:
      with self.assertRaises(ValueError):
        config_lib.AgentRuntimeConfig.from_sandboxes_yaml(path)
    finally:
      os.unlink(path)


class LoadSandboxIdsYamlTest(absltest.TestCase):

  def test_loads_correctly(self):
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.yaml', delete=False
    ) as f:
      f.write('sandbox_ids:\n  - sdx-aaa\n  - sdx-bbb\n')
      path = f.name
    try:
      ids = config_lib._load_sandbox_ids_yaml(path)
      self.assertEqual(ids, ['sdx-aaa', 'sdx-bbb'])
    finally:
      os.unlink(path)

  def test_file_not_found(self):
    with self.assertRaises(FileNotFoundError):
      config_lib._load_sandbox_ids_yaml('/does/not/exist.yaml')


class RegisterFlagsTest(absltest.TestCase):

  def test_register_flags_is_idempotent(self):
    config_lib.register_flags()
    config_lib.register_flags()  # second call should be a no-op

  def test_sandboxes_yaml_flag_registered(self):
    config_lib.register_flags()
    from absl import flags  # pylint: disable=g-import-not-at-top
    self.assertIn('sandboxes_yaml', flags.FLAGS)


class AgentRuntimeConfigFromFlagsTest(absltest.TestCase):
  """Tests for from_flags() — focus on the keep_sandbox fix."""

  def setUp(self):
    super().setUp()
    config_lib.register_flags()
    from absl import flags  # pylint: disable=g-import-not-at-top
    # Mark flags as parsed so from_flags() can read flag values.
    if not flags.FLAGS.is_parsed():
      flags.FLAGS.mark_as_parsed()

  def test_from_flags_sandboxes_yaml_sets_keep_sandbox(self):
    """from_flags() with --sandboxes_yaml must set keep_sandbox=True.

    Regression test for the bug where sandbox_ids_file was set but
    keep_sandbox was left as False, causing pre-created sandboxes to be
    killed at the end of a run.
    """
    from absl.testing import flagsaver  # pylint: disable=g-import-not-at-top

    yaml_path = '/some/sandboxes.yaml'
    env_vars = {
        'E2B_DOMAIN': 'test.tencentags.com',
        'E2B_API_KEY': 'test-key',
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
      with flagsaver.flagsaver(sandboxes_yaml=yaml_path):
        cfg = config_lib.AgentRuntimeConfig.from_flags()

    self.assertEqual(cfg.sandbox_ids_file, yaml_path)
    self.assertTrue(
        cfg.keep_sandbox,
        'keep_sandbox must be True when sandboxes_yaml is provided via flags',
    )

  def test_from_flags_no_sandboxes_yaml_keep_sandbox_default_false(self):
    """Without --sandboxes_yaml, keep_sandbox defaults to False."""
    from absl.testing import flagsaver  # pylint: disable=g-import-not-at-top

    env_vars = {
        'E2B_DOMAIN': 'test.tencentags.com',
        'E2B_API_KEY': 'test-key',
        'SANDBOX_TEMPLATE': 'android_world_test',
    }
    with mock.patch.dict(os.environ, env_vars, clear=False):
      with flagsaver.flagsaver(sandboxes_yaml=None):
        cfg = config_lib.AgentRuntimeConfig.from_flags()

    self.assertIsNone(cfg.sandbox_ids_file)
    self.assertFalse(cfg.keep_sandbox)


if __name__ == '__main__':
  absltest.main()
