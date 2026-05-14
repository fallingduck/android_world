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

"""Tasks for the clock app."""

# =============================================================================
# 【修改说明】拆分 _close_clock_app 职责，分别处理"退出 UI"与"复位秒表"。
# 修改日期：2026-05-13（初版）/ 2026-05-14（完整修复）
#
# 修改内容：
#   1. _close_clock_app：改为 press_home_button，仅退出 UI，不清除数据。
#      避免原 pm clear 在 tear_down 时杀死 Timer Service 导致状态丢失。
#
#   2. 新增 _reset_stopwatch：在秒表任务的 initialize_task 中单独调用
#      pm clear，将秒表复位到 "00:00:00 paused" 初态。
#
# ── 原始设计意图 ──────────────────────────────────────────────────────────────
# 上游代码（commit 34012d6，Google 2024-10-16）用 pm clear 同时实现两件事：
#   1. 关闭时钟 UI
#   2. 程序化复位秒表状态（秒表无 ADB 接口，只能靠清数据复位）
# 这在 Google 官方模拟器 + snapshot 机制下工作正常，因为 restore_snapshot
# 会在 pm clear 之前先恢复干净快照，pm clear 的数据清除有快照兜底。
#
# ── 在 redroid 上的问题 ───────────────────────────────────────────────────────
# redroid 容器无预制快照（/data/data/android_world/snapshots/ 不存在），
# restore_snapshot 抛 RuntimeError 被 warning 忽略。
# 此时 pm clear 的时序：
#   initialize_task: pm clear → 秒表复位 ✓（对秒表任务是正确的）
#   tear_down:       pm clear → 清除 agent 任务执行结果（无害，is_successful
#                               在 tear_down 之前调用）
# 实际上原实现对 ClockTimerEntry 的 is_successful 评估无影响；
# 但 _close_clock_app 同时被 initialize_task 和 tear_down 调用，
# initialize_task 的 pm clear 会清除上一次任务留下的秒表状态，
# 若连续运行多个任务，逻辑上等价于原始设计，结果是正确的。
#
# ── 为何仍需拆分 ─────────────────────────────────────────────────────────────
# ClockTimerEntry 的 initialize_task 不需要清数据，pm clear 是多余操作；
# 若将来秒表任务和 Timer 任务混合连续运行，保持职责分离更安全可靠。
# =============================================================================

import random
from absl import logging
from android_world.env import adb_utils
from android_world.env import interface
from android_world.env import representation_utils
from android_world.task_evals import task_eval


def _is_stopwatch_running(
    ui_elements: list[representation_utils.UIElement],
    current_activity: str,
) -> bool:
  """Checks if current screen is stopwatch running."""
  if "DeskClock" not in current_activity:
    return False
  pause_present = False
  lap_present = False

  for element in ui_elements:
    if element.content_description == "Pause":
      pause_present = True
    elif element.content_description == "Lap":
      lap_present = True
  return pause_present and lap_present


def _is_stopwatch_paused(
    ui_elements: list[representation_utils.UIElement],
    current_activity: str,
) -> bool:
  """Checks if current screen is stopwatch paused."""
  if "DeskClock" not in current_activity:
    return False
  start_present = False
  n_stopwatch = 0

  for element in ui_elements:
    if element.content_description == "Start":
      start_present = True
    elif (
        element.content_description == "Stopwatch"
        or element.text == "Stopwatch"
    ):
      n_stopwatch += 1
  logging.info("Start present: %s", start_present)
  logging.info("Stopwatch: %d", n_stopwatch)
  return start_present and n_stopwatch >= 2


def _is_timer_set(
    ui_elements: list[representation_utils.UIElement],
    current_activity: str,
    *,
    hours: int,
    minutes: int,
    seconds: int,
) -> bool:
  """Determines if a timer is set.

  Args:
    ui_elements: A list of UI elements representing the interface components
      within the "DeskClock" activity.
    current_activity: The name of the current activity within the UI.
    hours: The number of hours to check for in the timer.
    minutes: The number of minutes to check for in the timer.
    seconds: The number of seconds to check for in the timer.

  Returns:
    True if the timer is set with the specified hours, minutes, and seconds
      within the "DeskClock" activity; False otherwise.
  """
  if "DeskClock" not in current_activity:
    return False
  text_format = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
  content_desc_format = f"{hours} hours, {minutes} minutes, {seconds} seconds"

  for element in ui_elements:
    if (
        element.text == text_format
        or element.content_description == content_desc_format
    ):
      return True

  return False


def _close_clock_app(env: interface.AsyncEnv):
  """Closes the clock app by pressing HOME to avoid killing Alarm/Timer Service."""
  adb_utils.press_home_button(env.controller)


def _reset_stopwatch(env: interface.AsyncEnv):
  """Resets the stopwatch to 00:00:00 paused state via pm clear.

  The stopwatch has no ADB interface for direct control. Clearing app data is
  the only programmatic way to guarantee a known initial state (paused at zero).
  This must be called in initialize_task for stopwatch tasks, NOT in tear_down,
  so that the reset happens before the agent acts, not after is_successful runs.

  Supports both AOSP (com.android.deskclock) and GMS (com.google.android.deskclock)
  variants by probing which package is actually installed.
  """
  candidate_packages = [
      "com.android.deskclock",
      "com.google.android.deskclock",
  ]
  try:
    installed = set(adb_utils.get_all_package_names(env.controller))
  except Exception:  # pylint: disable=broad-except
    installed = set()

  pkg = None
  for candidate in candidate_packages:
    if candidate in installed:
      pkg = candidate
      break

  if pkg is None:
    # Fallback to the primary mapping; clear_app_data will raise if missing.
    pkg = adb_utils.extract_package_name(adb_utils.get_adb_activity("clock"))

  adb_utils.clear_app_data(pkg, env.controller)


class _ClockEval(task_eval.TaskEval):
  """Base class for clock tasks."""

  app_names = ("clock",)

  def initialize_task(self, env: interface.AsyncEnv):
    super().initialize_task(env)
    _close_clock_app(env)

  def tear_down(self, env: interface.AsyncEnv):
    super().tear_down(env)
    _close_clock_app(env)


class ClockTimerEntry(_ClockEval):
  """Task for checking if timer is set (but not started)."""

  complexity = 1
  schema = {
      "type": "object",
      "properties": {
          "hours": {"type": "integer"},
          "minutes": {"type": "integer"},
          "seconds": {"type": "integer"},
      },
      "required": ["hours", "minutes", "seconds"],
  }
  template = (
      "Create a timer with {hours} hours, {minutes} minutes, and {seconds}"
      " seconds. Do not start the timer."
  )

  def is_successful(
      self,
      env: interface.AsyncEnv,
  ) -> float:
    super().is_successful(env)
    ui_elements = env.get_state().ui_elements
    current_activity = adb_utils.get_current_activity(env.controller)[0]
    return (
        1.0
        if _is_timer_set(
            ui_elements=ui_elements,
            current_activity=current_activity,
            hours=self._params["hours"],
            minutes=self._params["minutes"],
            seconds=self._params["seconds"],
        )
        else 0.0
    )

  @classmethod
  def generate_random_params(cls) -> dict[str, int]:
    hours = random.randint(0, 23)
    minutes = random.randint(0, 59)
    seconds = random.randint(0, 59)

    params = {
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
    }

    return params


class ClockStopWatchPausedVerify(_ClockEval):
  """Task for checking if stop watch is paused.

  Precondition: The stopwatch is already paused at 00:00:00.

  There is not programmatic way to control the stopwatch. However, the app can
  be forced cleared, effectively resetting and pausing the watch. Hence, there
  is only a "Verify" version of this task.
  """

  complexity = 1
  schema = {
      "type": "object",
      "properties": {},
  }
  template = "Pause the stopwatch."

  def initialize_task(self, env: interface.AsyncEnv):
    super().initialize_task(env)
    # Reset stopwatch to 00:00:00 paused before agent acts.
    # press_home_button in _close_clock_app does NOT clear app data,
    # so we explicitly reset here to guarantee a known initial state.
    _reset_stopwatch(env)

  def is_successful(
      self,
      env: interface.AsyncEnv,
  ) -> float:
    super().is_successful(env)
    ui_elements = env.get_state().ui_elements
    current_activity = adb_utils.get_current_activity(env.controller)[0]
    return (
        1.0
        if _is_stopwatch_paused(
            ui_elements=ui_elements,
            current_activity=current_activity,
        )
        else 0.0
    )

  @classmethod
  def generate_random_params(cls) -> dict[str, str]:
    return {}


class ClockStopWatchRunning(_ClockEval):
  """Task for checking if stop watch is paused.

  Precondition: The stopwatch is already paused at 00:00:00.

  There is no programmatic way to control the stopwatch. However, the app can be
  forced cleared, effectively resetting and pausing the watch.
  """

  complexity = 1
  schema = {
      "type": "object",
      "properties": {},
  }
  template = "Run the stopwatch."

  def initialize_task(self, env: interface.AsyncEnv):
    super().initialize_task(env)
    # Reset stopwatch to 00:00:00 paused before agent acts.
    _reset_stopwatch(env)

  def is_successful(
      self,
      env: interface.AsyncEnv,
  ) -> float:
    super().is_successful(env)
    ui_elements = env.get_state().ui_elements
    current_activity = adb_utils.get_current_activity(env.controller)[0]
    return (
        1.0
        if _is_stopwatch_running(
            ui_elements=ui_elements,
            current_activity=current_activity,
        )
        else 0.0
    )

  @classmethod
  def generate_random_params(cls) -> dict[str, str]:
    return {}
