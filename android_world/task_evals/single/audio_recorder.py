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

"""Tasks for AudioRecorder app."""

import random
import time
from typing import Any

from absl import logging
from android_world.env import adb_utils
from android_world.env import device_constants
from android_world.env import interface
from android_world.task_evals import task_eval
from android_world.task_evals.common_validators import file_validators
from android_world.task_evals.utils import user_data_generation
from android_world.utils import file_utils

_PACKAGE = "com.dimowner.audiorecorder"


class _AudioRecorder(task_eval.TaskEval):
  """Base class for AudioRecorder tasks."""

  app_names = ("audio recorder",)

  def _ensure_audio_recorder_ready(self, env: interface.AsyncEnv) -> None:
    """Ensure Audio Recorder data directory exists by launching the app once."""
    if file_utils.check_directory_exists(
        device_constants.AUDIORECORDER_DATA, env.controller
    ):
      return
    logging.info(
        "Audio Recorder data directory missing, launching app to create it."
    )
    # Grant permissions.
    adb_utils.grant_permissions(
        _PACKAGE, "android.permission.RECORD_AUDIO", env.controller
    )
    adb_utils.grant_permissions(
        _PACKAGE, "android.permission.POST_NOTIFICATIONS", env.controller
    )
    # Launch the app to trigger directory creation.
    adb_utils.issue_generic_request(
        ["shell", "am", "start", "-n",
         f"{_PACKAGE}/.app.main.MainActivity"],
        env.controller,
    )
    time.sleep(3.0)
    # Close the app after initialization.
    adb_utils.close_app("audio recorder", env.controller)


class AudioRecorderRecordAudio(_AudioRecorder):
  """Task for checking that one audio recording has been completed."""

  complexity = 1.2
  schema = {
      "type": "object",
      "properties": {},
      "required": [],
  }
  template = "Record an audio clip using Audio Recorder app and save it."

  def initialize_task(self, env: interface.AsyncEnv) -> None:
    super().initialize_task(env)
    self._ensure_audio_recorder_ready(env)
    self.before_recording = file_utils.get_file_list_with_metadata(
        device_constants.AUDIORECORDER_DATA, env.controller
    )

  def is_successful(self, env: interface.AsyncEnv) -> float:
    super().is_successful(env)
    after_recording = [
        file
        for file in file_utils.get_file_list_with_metadata(
            device_constants.AUDIORECORDER_DATA, env.controller
        )
        if file.file_size > 0
    ]
    changed = []
    # Old recordings may be deleted and a new recording may reuse an existing
    # file name.
    for item in after_recording:
      if item not in self.before_recording:
        changed.append(item.file_name)
    logging.info("New or changed recording: %s", changed)

    # Check if a new audio recording is done by comparing directory contents
    one_new_file = len(changed) == 1
    return 1.0 if one_new_file else 0.0

  @classmethod
  def generate_random_params(cls) -> dict[str, str | int]:
    return {}


class AudioRecorderRecordAudioWithFileName(_AudioRecorder):
  """Task for checking that one audio recording with file_name has been completed."""

  complexity = 2
  schema = file_validators.CreateFile.schema
  template = (
      'Record an audio clip and save it with name "{file_name}" using Audio'
      " Recorder app."
  )

  def __init__(self, params: dict[str, Any]):
    """See base class."""
    super().__init__(params)
    self.initialized = False
    self.create_file_task = file_validators.CreateFile(
        params, device_constants.AUDIORECORDER_DATA
    )

  def _clear_audio_recorder_data(self, env: interface.AsyncEnv) -> None:
    """Clears all audio recorder data on device."""
    file_utils.clear_directory(
        device_constants.AUDIORECORDER_DATA, env.controller
    )

  def initialize_task(self, env: interface.AsyncEnv) -> None:
    super().initialize_task(env)
    self._ensure_audio_recorder_ready(env)
    self.create_file_task.initialize_task(env)
    self._clear_audio_recorder_data(env)

  def is_successful(self, env: interface.AsyncEnv) -> float:
    super().is_successful(env)
    file_name = self.params["file_name"]
    exists = file_utils.check_file_or_folder_exists(
        file_name + ".m4a", self.create_file_task.data_directory, env.controller
    )
    if not exists:
      logging.info("%s not found", file_name)
      return 0.0
    return 1.0

  @classmethod
  def generate_random_params(cls) -> dict[str, str | int]:
    name = [
        "interview",
        "meeting",
        "lecture",
        "session",
        "note",
        "conference",
        "webinar",
        "workshop",
        "seminar",
        "briefing",
        "discussion",
        "talk",
        "presentation",
        "training",
        "guidance",
        "memo",
        "narration",
        "storytelling",
        "journal",
        "diary",
        "debate",
        "symposium",
        "roundtable",
        "consultation",
        "review",
    ]
    return {
        "file_name": user_data_generation.generate_modified_file_name(
            random.choice(name) + ".m4a"
        ),
        "text": "",  # Unused.
    }

  def tear_down(self, env: interface.AsyncEnv) -> None:
    super().tear_down(env)
    self.create_file_task.tear_down(env)
    self._clear_audio_recorder_data(env)
