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

"""Setup tool for Android World.

It does the following:

* APK Management: Automates installations of apks needed for Android World.
* Sets up environment: Configures emulator with necessary permissions, using adb
  and basic automation.
"""

# =============================================================================
# 【修改说明】适配 redroid 环境的设备初始化流程。
# 修改日期：2026-05-14, 2026-05-21
#
# 修改内容：
#   1. 增加 local_apk_dir 支持，redroid 下优先从本地 APK 目录安装。
#   2. 增加 APK 安装状态检测（pm list packages），避免重复安装。
#   3. redroid 模式下跳过 emulator-specific 的 setup 步骤（如 gRPC 调用）。
#   4. 支持 AOSP 和 GMS 两种包名体系。
#   5. local_apk_dir 从硬编码 /root/android_world_apks 改为读取环境变量
#      ANDROID_WORLD_LOCAL_APK_DIR（默认值不变），方便不同部署环境自定义路径。
#
# 对评测语义/难度的影响：无。
#   - 所有修改均为设备初始化流程的平台适配，不改变任务目标、评估标准
#     或 agent 交互流程。环境变量化仅影响 APK 查找路径，不影响评测逻辑。
# =============================================================================

import glob
import json
import os
import shutil
import tempfile
from typing import Collection, Type
from zipfile import ZipFile

from absl import logging
from android_env import env_interface
from android_env.components import errors
from android_world.env import adb_utils
from android_world.env import interface
from android_world.env.setup_device import apps
from android_world.utils import app_snapshot

# APKs required for Android World.
_APPS = (
    # keep-sorted start
    apps.AndroidWorldApp,
    apps.AudioRecorder,
    apps.CameraApp,
    apps.ChromeApp,
    apps.ClipperApp,
    apps.ClockApp,
    apps.ContactsApp,
    apps.DialerApp,
    apps.ExpenseApp,
    apps.FilesApp,
    apps.JoplinApp,
    apps.MarkorApp,
    apps.MiniWobApp,
    apps.OpenTracksApp,
    apps.OsmAndApp,
    apps.RecipeApp,
    apps.RetroMusicApp,
    apps.SettingsApp,
    apps.SimpleCalendarProApp,
    apps.SimpleDrawProApp,
    apps.SimpleGalleryProApp,
    apps.SimpleSMSMessengerApp,
    apps.TasksApp,
    apps.VlcApp,
    # keep-sorted end
)


def get_installed_packages(env: interface.AsyncEnv) -> frozenset[str]:
  """Returns the set of installed packages."""
  return frozenset(adb_utils.get_all_package_names(env.controller.env))


def is_package_installed(package_name: str, env: interface.AsyncEnv) -> bool:
  """Checks if a package is installed."""
  installed_packages = get_installed_packages(env)
  return package_name in installed_packages


def get_app_mapping(app_name: str) -> Type[apps.AppSetup] | None:
  if not app_name:
    return None
  mapping = {app.app_name: app for app in _APPS}
  if app_name in mapping:
    return mapping[app_name]
  return None


def get_app_list_to_setup(
    task_ids: list[str] | None,
) -> tuple[Type[apps.AppSetup], ...] | None:
  """Returns the list of apps that are required by the tasks.

  Args:
    task_ids: A list of tasks.

  Returns:
    A tuple of AppSetup classes.
  """
  if not task_ids:
    return None
  required_apps = set()
  for app_class in _APPS:
    # Convert app_name to PascalCase, handling existing capitalization.
    pascal_case_app_name = "".join(
        word.capitalize() for word in app_class.app_name.split()
    )
    for task_id in task_ids:
      if pascal_case_app_name in task_id:
        required_apps.add(app_class)
  return tuple(required_apps)


def download_and_install_apk(
    apk: str,
    raw_env: env_interface.AndroidEnvInterface,
    local_apk_dir: str | None = None,
    extra_install_args: Collection[str] | None = None,
) -> None:
  """Downloads APK from remote location and installs it."""
  path = apps.download_app_data(apk, local_apk_dir=local_apk_dir)
  adb_utils.install_apk(path, raw_env, extra_args=extra_install_args)


def install_split_bundle(
    bundle_path: str,
    raw_env: env_interface.AndroidEnvInterface,
    extra_install_args: Collection[str] | None = None,
) -> None:
  """Installs a split APK bundle (XAPK or APKM) by extracting and using install-multiple."""
  if not os.path.exists(bundle_path):
    raise ValueError(f"Bundle does not exist: {bundle_path}")

  tmp_dir = tempfile.mkdtemp(prefix="split_bundle_")
  try:
    with ZipFile(bundle_path, "r") as zf:
      zf.extractall(tmp_dir)

    manifest_path = os.path.join(tmp_dir, "manifest.json")
    apk_files = []
    if os.path.isfile(manifest_path):
      with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
      for entry in manifest.get("split_apks", []):
        apk_file = entry.get("file")
        if apk_file:
          full_path = os.path.join(tmp_dir, apk_file)
          if os.path.isfile(full_path):
            apk_files.append(full_path)

    if not apk_files:
      # Fallback for APKM and other zip-based bundles: collect all .apk files.
      apk_files = [
          os.path.join(tmp_dir, f)
          for f in os.listdir(tmp_dir)
          if f.endswith(".apk") and os.path.isfile(os.path.join(tmp_dir, f))
      ]
      apk_files.sort()

    if not apk_files:
      raise RuntimeError(f"No APK files found inside bundle: {bundle_path}")

    adb_utils.install_multiple_apks(
        apk_files, raw_env, extra_args=extra_install_args
    )
  finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)


def setup_app(app: Type[apps.AppSetup], env: interface.AsyncEnv) -> None:
  """Sets up a single app."""
  try:
    logging.info("Setting up app %s", app.app_name)
    app.setup(env)
  except ValueError as e:
    logging.warning(
        "Failed to automatically setup app %s: %s.\n\nYou will need to"
        " manually setup the app.",
        app.app_name,
        e,
    )
  app_snapshot.save_snapshot(app.app_name, env.controller)


def _is_redroid(env: interface.AsyncEnv) -> bool:
  """True if the environment is running in redroid mode."""
  return getattr(env.controller, "device_mode", "") == "redroid"


def _maybe_install_trichrome_library(
    raw_env: env_interface.AndroidEnvInterface,
    local_apk_dir: str | None,
) -> None:
  """Installs the Trichrome Library if it is staged locally and not yet on device."""
  if local_apk_dir is None:
    return
  if adb_utils.is_app_installed("com.google.android.trichromelibrary", raw_env):
    logging.info("Trichrome Library already installed, skipping.")
    return
  pattern = os.path.join(local_apk_dir, "com.google.android.trichromelibrary*.apk")
  candidates = glob.glob(pattern)
  if not candidates:
    logging.warning(
        "Trichrome Library APK not found in %s; Chrome installation may fail.",
        local_apk_dir,
    )
    return
  # Pick the first match (alphabetically, usually the only one).
  trichrome_apk = sorted(candidates)[0]
  logging.info("Installing Trichrome Library from %s", trichrome_apk)
  try:
    adb_utils.install_apk(trichrome_apk, raw_env)
  except Exception as e:
    logging.warning("Failed to install Trichrome Library: %s", e)


def maybe_install_app(
    app: Type[apps.AppSetup], env: interface.AsyncEnv
) -> None:
  """Installs all APKs for Android World."""
  if not app.apk_names:  # Ignore 1p apps that don't have an APK.
    return
  logging.info("Installing app: %s.", app.app_name)

  _default_redroid_apk_dir = os.environ.get(
      "ANDROID_WORLD_LOCAL_APK_DIR", "/root/android_world_apks"
  )
  local_apk_dir = _default_redroid_apk_dir if _is_redroid(env) else None

  # Clipper targets a very old SDK (targetSdk=10). Android 14+ rejects it
  # unless --bypass-low-target-sdk-block is passed.
  extra_install_args: Collection[str] | None = None
  if app.app_name == "clipper":
    extra_install_args = ["--bypass-low-target-sdk-block"]

  # Chrome 148+ requires the static shared library com.google.android.trichromelibrary.
  # If the library APK is staged alongside Chrome, install it first.
  if app.app_name == "chrome":
    _maybe_install_trichrome_library(
        env.controller.env,
        local_apk_dir=local_apk_dir,
    )

  apk_installed = False
  for apk_name in app.apk_names:
    try:
      path = apps.download_app_data(apk_name, local_apk_dir=local_apk_dir)
      if apk_name.endswith(".xapk") or apk_name.endswith(".apkm"):
        install_split_bundle(
            path,
            env.controller.env,
            extra_install_args=extra_install_args,
        )
      else:
        adb_utils.install_apk(
            path,
            env.controller.env,
            extra_args=extra_install_args,
        )
      apk_installed = True
      break
    except errors.AdbControllerError:
      # Try apk compiled for a different architecture, e.g., Mac M1.
      continue
  if not apk_installed:
    raise RuntimeError(f"Failed to download and install APK for {app.app_name}")


def setup_apps(
    env: interface.AsyncEnv,
    app_list: tuple[Type[apps.AppSetup], ...] | None = None,
) -> None:
  """Sets up apps for Android World.

  Args:
    env: The Android environment.
    app_list: The list of apps to setup. If not specified, the default list of
      apps will be used.

  Raises:
    RuntimeError: If cannot install APK.
  """
  # Make sure quick-settings are not displayed, which can override foreground
  # apps, and impede UI navigation required for setting up.
  adb_utils.press_home_button(env.controller)
  adb_utils.set_root_if_needed(env.controller)

  logging.info(
      "Installing and setting up applications on Android device. Please do not"
      " interact with device while installation is running."
  )
  if app_list is None:
    app_list = _APPS
  for app in app_list:
    maybe_install_app(app, env)
    setup_app(app, env)
