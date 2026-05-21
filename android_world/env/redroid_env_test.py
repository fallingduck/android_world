import subprocess
from unittest import mock

from absl.testing import absltest
from android_env.components import errors as adb_errors
from android_env.proto import adb_pb2
from android_world.env import redroid_env


_ONE_BY_ONE_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0'
    b'\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82'
)


class RedroidEnvTest(absltest.TestCase):

  @mock.patch.object(redroid_env.adb_call_parser, 'AdbCallParser', autospec=True)
  @mock.patch.object(redroid_env.adb_controller, 'AdbController', autospec=True)
  def test_execute_adb_call_uses_adb_call_parser(
      self, mock_adb_controller, mock_adb_call_parser
  ):
    mock_controller = mock_adb_controller.return_value
    mock_controller.execute_command.return_value = _ONE_BY_ONE_PNG
    mock_parser = mock_adb_call_parser.return_value
    request = adb_pb2.AdbRequest(
        generic=adb_pb2.AdbRequest.GenericRequest(args=['shell', 'ls'])
    )

    env = redroid_env.RedroidEnv(
        adb_path='adb',
        device_name='localhost:5555',
    )
    env.execute_adb_call(request)

    mock_controller.init_server.assert_called_once()
    mock_parser.parse.assert_called_once_with(request)

  @mock.patch.object(redroid_env.adb_call_parser, 'AdbCallParser', autospec=True)
  @mock.patch.object(redroid_env.adb_controller, 'AdbController', autospec=True)
  def test_reset_returns_pixels(
      self, mock_adb_controller, mock_adb_call_parser
  ):
    del mock_adb_call_parser
    mock_controller = mock_adb_controller.return_value
    mock_controller.execute_command.return_value = _ONE_BY_ONE_PNG

    env = redroid_env.RedroidEnv(
        adb_path='adb',
        device_name='localhost:5555',
    )
    timestep = env.reset()

    self.assertEqual(timestep.observation['pixels'].shape, (1, 1, 3))
    self.assertEqual(timestep.observation['pixels'][0, 0].tolist(), [255, 0, 0])


class LooksLikeOfflineTest(absltest.TestCase):
  """Unit tests for `_looks_like_offline` — the guardrail that decides
  whether a failure is worth a costly ``adb kill-server`` bounce."""

  def test_device_not_found_is_offline(self):
    err = adb_errors.AdbControllerError(
        "adb: device 'localhost:25022' not found"
    )
    self.assertTrue(redroid_env._looks_like_offline(err))

  def test_device_offline_keyword(self):
    err = RuntimeError('error: device offline')
    self.assertTrue(redroid_env._looks_like_offline(err))

  def test_connection_reset_is_offline(self):
    err = RuntimeError('connection reset by peer')
    self.assertTrue(redroid_env._looks_like_offline(err))

  def test_legitimate_sqlite_error_is_not_offline(self):
    # This is exactly the failure mode from the SimpleSmsReply run where a
    # spurious reconnect would have masked the real problem (the provider's
    # databases dir does not exist because redroid has no Telephony stack).
    err = adb_errors.AdbControllerError(
        'Error: unable to open database '
        '"/data/data/com.android.providers.telephony/databases/mmssms.db": '
        'unable to open database file'
    )
    self.assertFalse(redroid_env._looks_like_offline(err))

  def test_permission_denied_is_not_offline(self):
    err = adb_errors.AdbControllerError('/system/bin/sh: denied')
    self.assertFalse(redroid_env._looks_like_offline(err))


class ResilientExecTest(absltest.TestCase):
  """Verifies `_build_resilient_exec` only reconnects on offline-looking
  failures and otherwise propagates exceptions untouched."""

  def test_passthrough_on_success(self):
    underlying = mock.Mock(return_value=b'ok')
    on_offline = mock.Mock()
    wrapped = redroid_env._build_resilient_exec(underlying, on_offline)
    self.assertEqual(wrapped(['shell', 'ls']), b'ok')
    underlying.assert_called_once_with(['shell', 'ls'])
    on_offline.assert_not_called()

  def test_reconnects_on_device_not_found(self):
    # First call fails with an offline-like error, second call succeeds.
    underlying = mock.Mock(
        side_effect=[
            adb_errors.AdbControllerError(
                "adb: device 'localhost:5555' not found"
            ),
            b'ok',
        ]
    )
    on_offline = mock.Mock()
    wrapped = redroid_env._build_resilient_exec(underlying, on_offline)
    result = wrapped(['shell', 'getprop', 'sys.boot_completed'])
    self.assertEqual(result, b'ok')
    on_offline.assert_called_once_with()
    self.assertEqual(underlying.call_count, 2)

  def test_does_not_reconnect_on_legitimate_error(self):
    # Exactly the failure mode from the SimpleSmsReply run — a spurious
    # reconnect would have masked a real Telephony-stack issue.
    sql_err = adb_errors.AdbControllerError(
        'Error: unable to open database '
        '"/data/data/com.android.providers.telephony/databases/mmssms.db": '
        'unable to open database file'
    )
    underlying = mock.Mock(side_effect=sql_err)
    on_offline = mock.Mock()
    wrapped = redroid_env._build_resilient_exec(underlying, on_offline)
    with self.assertRaises(adb_errors.AdbControllerError):
      wrapped(['shell', 'sqlite3', 'foo.db'])
    on_offline.assert_not_called()
    # Exactly one call — no retry was attempted.
    self.assertEqual(underlying.call_count, 1)

  def test_reconnects_on_called_process_error(self):
    # Older adb_controller versions propagate subprocess.CalledProcessError
    # directly. Make sure the wrapper catches that too when it looks like
    # an offline device.
    cpe = subprocess.CalledProcessError(
        returncode=1,
        cmd=['adb', 'shell', 'x'],
        output=b"adb: device 'localhost:5555' not found\n",
    )
    underlying = mock.Mock(side_effect=[cpe, b'ok'])
    on_offline = mock.Mock()
    wrapped = redroid_env._build_resilient_exec(underlying, on_offline)
    result = wrapped(['shell', 'ls'])
    self.assertEqual(result, b'ok')
    on_offline.assert_called_once_with()

  def test_propagates_second_attempt_failure(self):
    # Recovery hook ran but device is still unhappy. Wrapper must not loop.
    err = adb_errors.AdbControllerError(
        "adb: device 'localhost:5555' not found"
    )
    underlying = mock.Mock(side_effect=err)  # always fails
    on_offline = mock.Mock()
    wrapped = redroid_env._build_resilient_exec(underlying, on_offline)
    with self.assertRaises(adb_errors.AdbControllerError):
      wrapped(['shell', 'ls'])
    on_offline.assert_called_once_with()
    self.assertEqual(underlying.call_count, 2)


if __name__ == '__main__':
  absltest.main()
