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

"""Unit tests for ParallelRunner."""

import threading
from unittest import mock

from absl.testing import absltest
from android_world.runtime import parallel_runner as pr_lib


def _make_task(name='TestTask', seed=0, goal='Do something', complexity=1.0):
  task = mock.MagicMock()
  task.name = name
  task.goal = goal
  task.complexity = complexity
  task.is_successful.return_value = 1.0
  return task


def _make_instance(name='TestTask', seed=0, complexity=1.0):
  return pr_lib.TaskInstance(
      task_name=name,
      task=_make_task(name=name, seed=seed, complexity=complexity),
      seed=seed,
  )


class _FakeHandle:
  def __init__(self, sandbox_id='sb-0', slot_index=0):
    self.sandbox_id = sandbox_id
    env = mock.MagicMock()
    env.reset.return_value = None
    self.env = env
    self.controller = mock.MagicMock()
    self.controller._appium_env = mock.MagicMock()
    self.controller._appium_env.page_source.return_value = '<hierarchy/>'
    self.controller._appium_env.dump_logcat.return_value = ''
    self.artifact_writer = mock.MagicMock()
    self.slot_index = slot_index


class _FakePool:
  """Synchronous fake SandboxPool for testing."""

  def __init__(self, handles=None):
    self._handles = handles or [_FakeHandle()]
    self._idx = 0

  def __len__(self):
    return len(self._handles)

  def acquire(self, timeout=None):
    """Return a context manager that yields the next handle."""
    import contextlib  # pylint: disable=g-import-not-at-top
    handle = self._handles[self._idx % len(self._handles)]
    self._idx += 1

    @contextlib.contextmanager
    def _cm():
      yield handle

    return _cm()


class EpisodeResultTest(absltest.TestCase):

  def test_to_dict_keys(self):
    result = pr_lib.EpisodeResult(
        task_name='Foo', seed=1, is_successful=1.0, episode_length=5
    )
    d = result.to_dict()
    self.assertIn('task_name', d)
    self.assertIn('seed', d)
    self.assertIn('is_successful', d)
    self.assertIn('episode_length', d)

  def test_to_dict_values(self):
    result = pr_lib.EpisodeResult(
        task_name='Bar', seed=42, is_successful=0.0,
        episode_length=3, error='oops',
    )
    d = result.to_dict()
    self.assertEqual(d['task_name'], 'Bar')
    self.assertEqual(d['seed'], 42)
    self.assertEqual(d['is_successful'], 0.0)
    self.assertEqual(d['error'], 'oops')


class ParallelRunnerRunTest(absltest.TestCase):

  def _make_runner(self, pool=None, checkpointer=None, thread_pool_size=2):
    if pool is None:
      pool = _FakePool()
    cfg = mock.MagicMock()
    cfg.thread_pool_size = thread_pool_size
    cfg.max_concurrency = None
    cfg.dump_logcat_on_failure = False
    agent_factory = mock.MagicMock(return_value=mock.MagicMock())
    agent_factory.return_value.step.return_value = mock.MagicMock(done=True)
    return pr_lib.ParallelRunner(pool, agent_factory, cfg, checkpointer)

  def test_run_returns_one_result_per_instance(self):
    runner = self._make_runner()
    instances = [_make_instance('Task1', 0), _make_instance('Task2', 1)]
    results = runner.run(instances)
    self.assertEqual(len(results), 2)

  def test_run_successful_task(self):
    runner = self._make_runner()
    inst = _make_instance('Task1', 0)
    inst.task.is_successful.return_value = 1.0
    results = runner.run([inst])
    self.assertEqual(results[0].is_successful, 1.0)

  def test_run_failed_task_captured_as_error(self):
    handle = _FakeHandle()
    handle.env.reset.side_effect = RuntimeError('env died')
    pool = _FakePool(handles=[handle])
    runner = self._make_runner(pool=pool)
    inst = _make_instance('Task1', 0)
    results = runner.run([inst])
    self.assertEqual(len(results), 1)
    self.assertIsNotNone(results[0].error)
    self.assertEqual(results[0].is_successful, 0.0)

  def test_run_empty_instances_returns_empty(self):
    runner = self._make_runner()
    results = runner.run([])
    self.assertEqual(results, [])

  def test_run_with_checkpointer(self):
    checkpointer = mock.MagicMock()
    runner = self._make_runner(checkpointer=checkpointer)
    runner.run([_make_instance('T', 0)])
    checkpointer.save_episodes.assert_called_once()

  def test_checkpoint_writes_are_serialized(self):
    """Concurrent checkpoint writes should not interleave."""
    call_order = []
    lock = threading.Lock()

    checkpointer = mock.MagicMock()
    def _save_episodes(episodes, key):
      with lock:
        call_order.append(key)
    checkpointer.save_episodes.side_effect = _save_episodes

    handles = [_FakeHandle(f'sb-{i}', i) for i in range(3)]
    pool = _FakePool(handles=handles)
    runner = self._make_runner(pool=pool, checkpointer=checkpointer,
                               thread_pool_size=3)
    instances = [_make_instance(f'Task{i}', i) for i in range(6)]
    runner.run(instances)
    # All 6 instances should have been checkpointed exactly once.
    self.assertEqual(len(call_order), 6)

  def test_tear_down_called_even_on_failure(self):
    handle = _FakeHandle()
    handle.env.reset.side_effect = RuntimeError('crash')
    pool = _FakePool(handles=[handle])
    runner = self._make_runner(pool=pool)
    inst = _make_instance('Task1', 0)
    runner.run([inst])
    inst.task.tear_down.assert_called_once()


if __name__ == '__main__':
  absltest.main()
