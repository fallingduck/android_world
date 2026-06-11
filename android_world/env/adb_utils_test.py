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

"""Tests for adb_utils."""

from unittest import mock

from absl.testing import absltest
from android_env import env_interface
from android_env.proto import adb_pb2
from android_world.env import adb_utils


class AdbTestSetup(absltest.TestCase):

  def setUp(self):
    super().setUp()
    self.mock_env = mock.patch.object(
        env_interface, 'AndroidEnvInterface', autospec=True
    ).start()
    self.mock_issue_generic_request = mock.patch.object(
        adb_utils, 'issue_generic_request', autospec=True
    ).start()

  def tearDown(self):
    super().tearDown()
    mock.patch.stopall()


class PhoneUtilsTest(AdbTestSetup):

  def test_get_call_state_idle(self):
    dumpsys_content = """last known state:
Phone Id=0
  mCallState=0
  mRingingCallState=0
  mForegroundCallState=0"""
    # Setup
    mock_dumpsys_response = adb_pb2.AdbResponse()
    mock_dumpsys_response.generic.output = dumpsys_content.encode('utf-8')
    self.mock_issue_generic_request.return_value = mock_dumpsys_response

    # Act
    result = adb_utils.get_call_state(self.mock_env)

    # Assert
    self.assertEqual(result, 'IDLE')

  def test_get_call_state_ringing(self):
    dumpsys_content = """last known state:
Phone Id=0
  mCallState=2
  mRingingCallState=0
  mForegroundCallState=0"""
    # Setup
    mock_dumpsys_response = adb_pb2.AdbResponse()
    mock_dumpsys_response.generic.output = dumpsys_content.encode('utf-8')
    self.mock_issue_generic_request.return_value = mock_dumpsys_response

    # Act
    result = adb_utils.get_call_state(self.mock_env)

    # Assert
    self.assertEqual(result, 'OFFHOOK')

  def test_call_emulator(self):
    mock_response = adb_pb2.AdbResponse()
    mock_response.generic.output = b'Success'
    self.mock_issue_generic_request.return_value = mock_response

    phone_number = '+123456789'
    result = adb_utils.call_emulator(self.mock_env, phone_number)

    self.assertEqual(result.generic.output.decode(), 'Success')

  def test_call_emulator_redroid_is_unsupported(self):
    self.mock_env.device_mode = 'redroid'

    with self.assertRaises(NotImplementedError):
      adb_utils.call_emulator(self.mock_env, '+123456789')

  @mock.patch.object(adb_utils, 'get_call_state', autospec=True)
  def test_end_call_if_active(self, mock_get_call_state):
    mock_get_call_state.return_value = 'OFFHOOK'
    adb_utils.end_call_if_active(self.mock_env)

    self.mock_issue_generic_request.assert_called()

  def test_clear_android_emulator_call_log(self):
    adb_utils.clear_android_emulator_call_log(self.mock_env)
    self.mock_issue_generic_request.assert_called()

  def test_call_phone_number(self):
    mock_response = adb_pb2.AdbResponse()
    mock_response.generic.output = b'Success'
    self.mock_issue_generic_request.return_value = mock_response

    phone_number = '123456789'
    result = adb_utils.call_phone_number(self.mock_env, phone_number)

    self.assertEqual(result.generic.output.decode(), 'Success')

  def test_text_emulator(self):
    mock_response = adb_pb2.AdbResponse()
    mock_response.generic.output = b'Success'
    self.mock_issue_generic_request.return_value = mock_response

    phone_number = '+123456789'
    message = 'Hello, world!'
    result = adb_utils.text_emulator(self.mock_env, phone_number, message)

    self.assertEqual(result.generic.output.decode(), 'Success')


class MockLocationUtilsTest(AdbTestSetup):

  def test_enable_mock_location_permission_grants_shell_and_uid0(self):
    shell_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    uid0_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    self.mock_issue_generic_request.side_effect = [shell_response, uid0_response]

    result = adb_utils.enable_mock_location_permission(self.mock_env)

    self.assertIs(result, shell_response)
    self.assertEqual(self.mock_issue_generic_request.call_count, 2)
    self.mock_issue_generic_request.assert_has_calls([
        mock.call(
            [
                'shell',
                'appops',
                'set',
                'com.android.shell',
                'android:mock_location',
                'allow',
            ],
            self.mock_env,
            adb_utils._DEFAULT_TIMEOUT_SECS,
        ),
        mock.call(
            [
                'shell',
                'appops',
                'set',
                '--uid',
                '0',
                'android:mock_location',
                'allow',
            ],
            self.mock_env,
            adb_utils._DEFAULT_TIMEOUT_SECS,
        ),
    ])

  def test_enable_mock_location_permission_succeeds_when_uid0_only_works(self):
    shell_response = adb_pb2.AdbResponse(
        status=adb_pb2.AdbResponse.Status.INTERNAL_ERROR
    )
    uid0_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    self.mock_issue_generic_request.side_effect = [shell_response, uid0_response]

    result = adb_utils.enable_mock_location_permission(self.mock_env)

    self.assertEqual(result.status, adb_pb2.AdbResponse.Status.OK)

  def test_enable_mock_location_permission_fails_when_all_grants_fail(self):
    shell_response = adb_pb2.AdbResponse(
        status=adb_pb2.AdbResponse.Status.INTERNAL_ERROR
    )
    uid0_response = adb_pb2.AdbResponse(
        status=adb_pb2.AdbResponse.Status.INTERNAL_ERROR
    )
    self.mock_issue_generic_request.side_effect = [shell_response, uid0_response]

    result = adb_utils.enable_mock_location_permission(self.mock_env)

    self.assertEqual(result.status, adb_pb2.AdbResponse.Status.INTERNAL_ERROR)

  def test_setup_test_location_provider_happy_path(self):
    remove_response = adb_pb2.AdbResponse(
        status=adb_pb2.AdbResponse.Status.INTERNAL_ERROR
    )
    add_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    enable_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    self.mock_issue_generic_request.side_effect = [
        remove_response,
        add_response,
        enable_response,
    ]

    result = adb_utils.setup_test_location_provider(self.mock_env)

    self.assertIs(result, enable_response)
    self.assertEqual(self.mock_issue_generic_request.call_count, 3)
    self.mock_issue_generic_request.assert_has_calls([
        mock.call(
            [
                'shell',
                'cmd',
                'location',
                'providers',
                'remove-test-provider',
                'aw_gps',
            ],
            self.mock_env,
            adb_utils._DEFAULT_TIMEOUT_SECS,
        ),
        mock.call(
            [
                'shell',
                'cmd',
                'location',
                'providers',
                'add-test-provider',
                'aw_gps',
            ],
            self.mock_env,
            adb_utils._DEFAULT_TIMEOUT_SECS,
        ),
        mock.call(
            [
                'shell',
                'cmd',
                'location',
                'providers',
                'set-test-provider-enabled',
                'aw_gps',
                'true',
            ],
            self.mock_env,
            adb_utils._DEFAULT_TIMEOUT_SECS,
        ),
    ])

  def test_setup_test_location_provider_returns_add_failure(self):
    remove_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    add_response = adb_pb2.AdbResponse(
        status=adb_pb2.AdbResponse.Status.INTERNAL_ERROR
    )
    self.mock_issue_generic_request.side_effect = [remove_response, add_response]

    result = adb_utils.setup_test_location_provider(self.mock_env)

    self.assertIs(result, add_response)
    self.assertEqual(self.mock_issue_generic_request.call_count, 2)

  def test_set_test_location(self):
    mock_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    self.mock_issue_generic_request.return_value = mock_response

    result = adb_utils.set_test_location(47.141, 9.521, self.mock_env)

    self.assertIs(result, mock_response)
    self.mock_issue_generic_request.assert_called_once_with(
        [
            'shell',
            'cmd',
            'location',
            'providers',
            'set-test-provider-location',
            'aw_gps',
            '--location',
            '47.141,9.521',
            '--accuracy',
            '5.0',
        ],
        self.mock_env,
        adb_utils._DEFAULT_TIMEOUT_SECS,
    )

  def test_remove_test_location_provider(self):
    mock_response = adb_pb2.AdbResponse(status=adb_pb2.AdbResponse.Status.OK)
    self.mock_issue_generic_request.return_value = mock_response

    result = adb_utils.remove_test_location_provider(self.mock_env)

    self.assertIs(result, mock_response)
    self.mock_issue_generic_request.assert_called_once_with(
        [
            'shell',
            'cmd',
            'location',
            'providers',
            'remove-test-provider',
            'aw_gps',
        ],
        self.mock_env,
        adb_utils._DEFAULT_TIMEOUT_SECS,
    )


class AdbSettingsTest(AdbTestSetup):

  def test_set_default_app(self):
    mock_response = adb_pb2.AdbResponse()
    mock_response.generic.output = b'Success'
    self.mock_issue_generic_request.return_value = mock_response

    setting_key = 'sms_default_application'
    package_name = 'com.example.app'
    result = adb_utils.set_default_app(self.mock_env, setting_key, package_name)

    self.assertEqual(result.generic.output.decode(), 'Success')

  def test_successful_put_operation(self):
    self.mock_env.execute_adb_call.return_value = adb_pb2.AdbResponse()

    # Execute the function
    response = adb_utils.put_settings(
        namespace=adb_pb2.AdbRequest.SettingsRequest.Namespace.SYSTEM,
        key='example_key',
        value='example_value',
        env=self.mock_env,
    )

    # Assertions
    self.assertIsInstance(response, adb_pb2.AdbResponse)
    self.mock_env.execute_adb_call.assert_called_once()

  def test_invalid_inputs_put_operation(self):
    self.mock_env.execute_adb_call.return_value = adb_pb2.AdbResponse()

    # Invalid namespace (non-enum value)
    with self.assertRaises(ValueError):
      adb_utils.put_settings(
          namespace='INVALID',  # This should be an enum, not a string
          key='example_key',
          value='example_value',
          env=self.mock_env,
      )

    # Empty key
    with self.assertRaises(ValueError):
      adb_utils.put_settings(
          namespace=adb_pb2.AdbRequest.SettingsRequest.Namespace.SYSTEM,
          key='',
          value='example_value',
          env=self.mock_env,
      )

    # Empty value
    with self.assertRaises(ValueError):
      adb_utils.put_settings(
          namespace=adb_pb2.AdbRequest.SettingsRequest.Namespace.SYSTEM,
          key='example_key',
          value='',
          env=self.mock_env,
      )


class AdbTypingTest(AdbTestSetup):

  def test_can_type_text(self):
    with mock.patch.object(
        env_interface.AndroidEnvInterface, 'execute_adb_call'
    ) as mock_execute_adb_call:
      mock_execute_adb_call.return_value = adb_pb2.AdbResponse(
          status=adb_pb2.AdbResponse.Status.OK
      )
      adb_utils.type_text('Type some\ntext', self.mock_env)
      expected_calls = [
          mock.call(
              adb_pb2.AdbRequest(
                  input_text=adb_pb2.AdbRequest.InputText(text='Type'),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
          mock.call(
              adb_pb2.AdbRequest(
                  input_text=adb_pb2.AdbRequest.InputText(text='%s'),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
          mock.call(
              adb_pb2.AdbRequest(
                  input_text=adb_pb2.AdbRequest.InputText(text='some'),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
          mock.call(
              adb_pb2.AdbRequest(
                  press_button=adb_pb2.AdbRequest.PressButton(
                      button=adb_pb2.AdbRequest.PressButton.ENTER
                  ),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
          mock.call(
              adb_pb2.AdbRequest(
                  input_text=adb_pb2.AdbRequest.InputText(text='text'),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
      ]
      mock_execute_adb_call.assert_has_calls(expected_calls)

  def test_type_white_space(self):
    with mock.patch.object(
        env_interface.AndroidEnvInterface, 'execute_adb_call'
    ) as mock_execute_adb_call:
      mock_execute_adb_call.return_value = adb_pb2.AdbResponse(
          status=adb_pb2.AdbResponse.Status.OK
      )
      adb_utils.type_text(' ', self.mock_env)
      expected_calls = [
          mock.call(
              adb_pb2.AdbRequest(
                  input_text=adb_pb2.AdbRequest.InputText(text='%s'),
                  timeout_sec=adb_utils._DEFAULT_TIMEOUT_SECS,
              )
          ),
      ]
      mock_execute_adb_call.assert_has_calls(expected_calls)
      self.assertLen(expected_calls, mock_execute_adb_call.call_count)


class TestExtractBroadcastData(absltest.TestCase):

  def test_successful_data_extraction(self):
    raw_output = 'Broadcast completed: result=-1, data="Test data"\n'
    expected_result = 'Test data'
    result = adb_utils.extract_broadcast_data(raw_output)
    self.assertEqual(result, expected_result)

  def test_result_zero_returns_none(self):
    raw_output = 'Broadcast completed: result=0\n'
    result = adb_utils.extract_broadcast_data(raw_output)
    self.assertIsNone(result)

  def test_unexpected_output_raises_error(self):
    raw_output = 'Unexpected output format'
    with self.assertRaises(ValueError):
      adb_utils.extract_broadcast_data(raw_output)


class TestScreenUtils(absltest.TestCase):

  def test_parse_screen_size_response_success(self):
    """Test successful parsing of screen size from adb response."""
    response = 'Physical size: 1080x2400'
    expected_size = (1080, 2400)
    self.assertEqual(
        adb_utils._parse_screen_size_response(response), expected_size
    )

  def test_parse_screen_size_response_failure(self):
    """Test parsing failure when adb response is in an unexpected format."""
    response = 'Invalid response format'
    with self.assertRaises(ValueError) as context:
      adb_utils._parse_screen_size_response(response)
    self.assertIn(
        'Screen size information not found in adb response',
        str(context.exception),
    )


class LaunchAppFallbackTest(AdbTestSetup):
  """Covers `_resolve_fallback_activity` + `launch_app` fallback path.

  The `files` app has Google / AOSP alternatives (see
  `adb_utils._FALLBACK_ACTIVITIES`). `launch_app` must pick whichever package
  is actually installed on the device so that redroid (AOSP documentsui) and
  Google-signed images both work with the same task code.
  """

  def setUp(self):
    super().setUp()
    self.mock_get_all_package_names = mock.patch.object(
        adb_utils, 'get_all_package_names', autospec=True
    ).start()
    self.mock_start_activity = mock.patch.object(
        adb_utils, 'start_activity', autospec=True
    ).start()

  def test_files_prefers_google_nbu_when_installed(self):
    self.mock_get_all_package_names.return_value = [
        'com.google.android.apps.nbu.files',
        'com.android.documentsui',
    ]
    adb_utils.launch_app('files', self.mock_env)
    self.mock_start_activity.assert_called_once()
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertTrue(
        called_activity.startswith('com.google.android.apps.nbu.files/'),
        f'Expected NBU Files but got {called_activity!r}',
    )

  def test_files_falls_back_to_google_documentsui(self):
    self.mock_get_all_package_names.return_value = [
        'com.google.android.documentsui',
        'com.android.documentsui',
    ]
    adb_utils.launch_app('files', self.mock_env)
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertTrue(
        called_activity.startswith('com.google.android.documentsui/'),
        f'Expected Google DocumentsUI but got {called_activity!r}',
    )

  def test_files_falls_back_to_aosp_documentsui_on_redroid(self):
    # Redroid / AOSP: neither Google Files package is installed.
    self.mock_get_all_package_names.return_value = [
        'com.android.documentsui',
        'com.android.settings',
    ]
    adb_utils.launch_app('files', self.mock_env)
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertEqual(
        called_activity,
        'com.android.documentsui/com.android.documentsui.files.FilesActivity',
    )

  def test_files_without_any_candidate_uses_default_activity(self):
    # Pathological case: device is missing *all* listed candidates. We
    # should still issue a launch request, falling through to the default
    # activity from _PATTERN_TO_ACTIVITY so the user gets a clear error
    # from Android rather than a silent no-op.
    self.mock_get_all_package_names.return_value = ['com.android.settings']
    adb_utils.launch_app('files', self.mock_env)
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertEqual(
        called_activity,
        'com.google.android.documentsui/com.android.documentsui.files.FilesActivity',
    )

  def test_package_listing_failure_falls_back_to_default(self):
    # If `pm list packages` throws for any reason, we must not crash
    # `launch_app` — degrade to the default activity from
    # _PATTERN_TO_ACTIVITY. This mirrors the behaviour before the fallback
    # was introduced.
    self.mock_get_all_package_names.side_effect = RuntimeError('adb down')
    adb_utils.launch_app('files', self.mock_env)
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertEqual(
        called_activity,
        'com.google.android.documentsui/com.android.documentsui.files.FilesActivity',
    )

  def test_apps_without_fallback_are_untouched(self):
    # An app not listed in _FALLBACK_ACTIVITIES should never trigger a
    # package listing call and should launch its default activity.
    adb_utils.launch_app('markor', self.mock_env)
    self.mock_get_all_package_names.assert_not_called()
    called_activity = self.mock_start_activity.call_args.args[0]
    self.assertEqual(
        called_activity,
        'net.gsantner.markor/net.gsantner.markor.activity.MainActivity',
    )


if __name__ == '__main__':
  absltest.main()
