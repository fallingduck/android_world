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

"""Launches the environment used in the benchmark."""

import enum
import platform
from typing import Optional

from absl import logging
from android_world.env import android_world_controller
from android_world.env import interface
from android_world.utils import datetime_utils


# AndroidWorld is tested and developed on Pixel 6 with API 33. Other
# configurations may be supported, but are not tested.
_ANDROID_WORLD_API_LEVEL = 33


class DeviceMode(str, enum.Enum):
  EMULATOR = 'emulator'
  REDROID = 'redroid'
  AGENTRUNTIME = 'agentruntime'


def _normalize_device_mode(device_mode: str | DeviceMode) -> DeviceMode:
  if isinstance(device_mode, DeviceMode):
    return device_mode
  return DeviceMode(device_mode)


def _get_env(
    console_port: int,
    adb_path: str,
    grpc_port: int,
    device_mode: str | DeviceMode = DeviceMode.EMULATOR,
    adb_device_name: str | None = None,
    a11y_method: android_world_controller.A11yMethod | None = None,
    agentruntime_config=None,
) -> interface.AsyncEnv:
  """Creates an AsyncEnv by connecting to an existing Android environment."""
  device_mode = _normalize_device_mode(device_mode)

  if device_mode == DeviceMode.AGENTRUNTIME:
    # Import lazily so that Appium / E2B deps are optional for users who only
    # use the emulator or redroid backends.
    from android_world.env.agentruntime import appium_controller as ac  # pylint: disable=g-import-not-at-top
    controller = ac.create_appium_controller(agentruntime_config)
    return interface.AsyncAndroidEnv(controller)

  controller = android_world_controller.get_controller(
      console_port, adb_path, grpc_port
  )
  return interface.AsyncAndroidEnv(controller)


def _increase_file_descriptor_limit(limit: int = 32768):
  """Increases the file descriptor limit to the given limit.

  This helps with different platforms having different limits, which can result
  from too many open files, sockets, or pipes, resulting in "OSError: [Errno 24]
  Too many open files".

  Limit will only be increased if it is higher than the currently set limit.

  Args:
    limit: The new file descriptor limit. The default value was determined
      experimentally to not raise too many open files error.
  """
  system_name = platform.system()
  if system_name == 'Windows':
    return

  try:
    import resource  # pylint: disable=g-import-not-at-top

    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if limit > hard:
      logging.warning(
          (
              "Requested limit %d exceeds the system's hard limit %d. Setting"
              ' to the maximum allowed value.'
          ),
          limit,
          hard,
      )
      limit = hard

    current_soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    if current_soft_limit < limit:
      resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
      logging.info('File descriptor limit set to %d.', limit)
  except ValueError as e:
    logging.exception('Failed to set file descriptor limit: %s', e)


def setup_env(
    env: interface.AsyncEnv,
    emulator_setup: bool = False,
    freeze_datetime: bool = True,
) -> None:
  """Performs environment setup and validation."""
  _increase_file_descriptor_limit()
  if emulator_setup:
    logging.info('Setting up apps on the emulator.')
    from android_world.env.setup_device import setup  # pylint: disable=g-import-not-at-top
    setup.setup_apps(env)
  if freeze_datetime:
    logging.info('Freezing datetime.')
    datetime_utils.setup_datetime(env.controller)


def load_and_setup_env(
    console_port: int = 5554,
    emulator_setup: bool = False,
    freeze_datetime: bool = True,
    adb_path: str = android_world_controller.DEFAULT_ADB_PATH,
    grpc_port: int = 8554,
    device_mode: str | DeviceMode = DeviceMode.EMULATOR,
    adb_device_name: str | None = None,
    a11y_method: android_world_controller.A11yMethod | None = None,
    agentruntime_config=None,
) -> interface.AsyncEnv:
  """Create environment with `get_env()` and perform env setup and validation.

  Before running this, an emulator must be launched. For example:

  ```
  AVD_NAME=Pixel_6_API_33  # First create an AVD in Android Studio.
  ~/Android/Sdk/emulator/emulator -avd $AVD_NAME -no-snapshot -grpc 8554
  ```

  Args:
    console_port: The console port of the existing device. This can usually be
      retrieved by looking at the output of `adb devices`. In general, the first
      connected device is port 5554, the second is 5556, and so on.
    emulator_setup: Perform first-time app setup on the environment if True.
    freeze_datetime: Whether to freeze the datetime to a fixed time, October
      2023, to ensure consistent benchmarking.
    adb_path: The location of the adb binary.
    grpc_port: The port for gRPC communication with the emulator.
    device_mode: Which device backend to use.
    adb_device_name: Explicit adb serial/endpoint, required for redroid mode.
    a11y_method: Optional accessibility retrieval method override.
    agentruntime_config: ``AgentRuntimeConfig`` instance, required when
      ``device_mode=DeviceMode.AGENTRUNTIME``.

  Returns:
    An interactable Android environment.
  """
  device_mode = _normalize_device_mode(device_mode)

  # AgentRuntime mode skips emulator_setup and freeze_datetime by default
  # because those operations rely on ADB-level device control that is not
  # available in the Appium-only pipeline.
  if device_mode == DeviceMode.AGENTRUNTIME:
    env = _get_env(
        console_port,
        adb_path,
        grpc_port,
        device_mode=device_mode,
        adb_device_name=adb_device_name,
        a11y_method=a11y_method,
        agentruntime_config=agentruntime_config,
    )
    _increase_file_descriptor_limit()
    # Note: freeze_datetime and emulator_setup are intentionally skipped for
    # agentruntime mode.  Time control can be attempted through mobile:shell,
    # but is not guaranteed to work; tasks that are time-sensitive will need
    # individual handling.
    return env

  env = _get_env(
      console_port,
      adb_path,
      grpc_port,
  )
  setup_env(env, emulator_setup, freeze_datetime)
  return env
