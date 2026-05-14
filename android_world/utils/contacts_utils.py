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

"""Utils for contacts operations using adb."""

# =============================================================================
# 【修改说明】适配 redroid 中文环境的联系人保存按钮。
# 修改日期：2026-05-14
#
# 修改内容：
#   add_contact 中 find_and_click_element("SAVE") 改为先尝试 "SAVE"
#   （英文 locale），失败后尝试 "保存"（中文 locale），都失败则抛异常。
#
# 修改原因：
#   AOSP Contacts 应用了系统 locale。Emulator 默认 locale=en，按钮文本
#   为 "SAVE"；redroid 容器默认 locale=zh-CN，按钮文本为 "保存"。
#   原始代码硬编码 "SAVE"，在中文 locale 下必然失败。
#
# 对评测语义/难度的影响：无。
#   - 仅适配 UI 文本的 locale 差异，功能行为等价（都是点击保存按钮）。
#   - 不改变任务目标、评估标准或 agent 交互流程。
# =============================================================================

import dataclasses
import re
import time
from typing import Iterator

from android_world.env import actuation
from android_world.env import adb_utils
from android_world.env import android_world_controller


def clean_phone_number(phone_number: str) -> str:
  """Removes all non-numeric characters from a phone number.

  Args:
    phone_number: The phone number to clean.

  Returns:
    The phone number with all non-numeric characters removed.
  """
  return re.sub(r"\D", "", phone_number)


def add_contact(
    name: str,
    phone_number: str,
    env: android_world_controller.AndroidWorldController,
    ui_delay_sec: float = 1.0,
):
  """Adds a contact with the specified name and phone number.

  This function sends an intent to the Android system to add a contact with
  the information pre-filled, clicks the "Save" button to create it, and then
  returns from the activity.

  Args:
    name: The name of the new contact
    phone_number: The phone number belonging to that contact.
    env: The android environment to add the contact to.
    ui_delay_sec: Delay between UI interactions. If this value is too low, the
      "save" button may be mis-clicked.
  """
  intent_command = (
      "am start -a android.intent.action.INSERT -t"
      f' vnd.android.cursor.dir/contact -e name "{name}" -e phone'
      f" {phone_number}"
  )

  adb_command = ["shell", intent_command]
  adb_utils.issue_generic_request(adb_command, env)
  time.sleep(ui_delay_sec)
  # AOSP Contacts localizes the action button. Try the English label first
  # (Pixel/AVD with locale=en), then fall back to common locale variants.
  # On redroid containers shipped with locale=zh-CN the button reads "保存".
  for label in ("SAVE", "保存"):
    try:
      actuation.find_and_click_element(label, env)
      break
    except ValueError:
      continue
  else:
    raise ValueError(
        'Could not find Save / 保存 button in Contacts add-contact dialog.'
    )
  time.sleep(ui_delay_sec)
  adb_utils.press_back_button(env)
  time.sleep(ui_delay_sec)


@dataclasses.dataclass(frozen=True)
class Contact:
  """Basic contact information."""
  name: str
  number: str


def list_contacts(
    env: android_world_controller.AndroidWorldController,
) -> list[Contact]:
  """Lists all contacts available in the Android environment.

  Args:
    env: Android environment to search for contacts.

  Returns:
    A list of all contact names and numbers present on the device.
  """
  intent_command = (
      "content query --uri content://contacts/phones/ --projection"
      " display_name:number"
  )
  adb_command = ["shell", intent_command]

  def parse(adb_output: str) -> Iterator[Contact]:
    for match in re.finditer(r"display_name=(.*), number=(.*)", adb_output):
      yield Contact(match.group(1), clean_phone_number(match.group(2)))

  return list(
      parse(
          adb_utils.issue_generic_request(
              adb_command, env
          ).generic.output.decode("utf-8")
      )
  )


def clear_contacts(env: android_world_controller.AndroidWorldController):
  """Clears all contacts on the device."""
  adb_utils.clear_app_data("com.android.providers.contacts", env)
