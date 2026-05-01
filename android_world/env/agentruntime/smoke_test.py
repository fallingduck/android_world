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

"""Pre-flight capability smoke-test for the AgentRuntime / Appium backend.

Run this script against a live E2B sandbox **before** executing a full
benchmark to verify that all five capability pillars required by
AndroidWorld tasks are working correctly:

  1. Screenshot (PNG → numpy RGB)
  2. UI XML dump / page-source (used for a11y-based validators)
  3. Shell execution via ``mobile: shell`` (pm / am / settings / etc.)
  4. File push + pull round-trip (database validators require this)
  5. App lifecycle (launch, query state, terminate)

Usage::

  python -m android_world.env.agentruntime.smoke_test \\
      --agentruntime_domain=<domain> \\
      --agentruntime_api_key=<key> \\
      --agentruntime_sandbox_id=<id>   # or --agentruntime_template=<tpl>

All five checks must pass for the environment to be considered valid.
Any failure is printed with a traceback and the exit code is 1.
"""

from __future__ import annotations

import sys
import textwrap
import traceback

from absl import app
from absl import flags
from absl import logging

from android_world.env.agentruntime import appium_controller as ac_lib
from android_world.env.agentruntime import config as config_lib

# Re-use the AgentRuntime CLI flags so the smoke test can be driven by
# the same --agentruntime_* family of flags as run.py.
config_lib.register_flags()

FLAGS = flags.FLAGS

_PASS = '  [PASS]'
_FAIL = '  [FAIL]'


def _check_screenshot(appium_env) -> tuple[bool, str]:
  """Check 1: screenshot returns a non-empty RGB numpy array."""
  import numpy as np  # pylint: disable=g-import-not-at-top
  pixels = appium_env.screenshot()
  if not isinstance(pixels, np.ndarray):
    return False, f'Expected np.ndarray, got {type(pixels)}'
  if pixels.ndim != 3 or pixels.shape[2] != 3:
    return False, f'Expected shape (H, W, 3), got {pixels.shape}'
  if pixels.size == 0:
    return False, 'Screenshot array is empty.'
  return True, f'OK — shape={pixels.shape}, dtype={pixels.dtype}'


def _check_page_source(appium_env) -> tuple[bool, str]:
  """Check 2: page_source returns non-empty XML."""
  xml = appium_env.page_source()
  if not isinstance(xml, str) or len(xml) < 20:
    return False, f'page_source too short or wrong type: {xml!r:.80}'
  if '<' not in xml:
    return False, f'page_source does not look like XML: {xml!r:.80}'
  return True, f'OK — {len(xml)} chars, starts with {xml[:60]!r}'


def _check_shell(appium_env) -> tuple[bool, str]:
  """Check 3: mobile: shell executes a basic pm list packages command."""
  output = appium_env.shell('pm list packages android')
  if 'package:android' not in output:
    return (
        False,
        f'Expected "package:android" in pm output, got: {output!r:.120}',
    )
  return True, f'OK — "pm list packages android" returned {len(output)} chars'


def _check_file_round_trip(appium_env) -> tuple[bool, str]:
  """Check 4: push a file then pull it back and compare bytes."""
  import os  # pylint: disable=g-import-not-at-top
  import tempfile  # pylint: disable=g-import-not-at-top

  payload = b'android_world_smoke_test_payload_\x00\x01\x02\xff'
  remote_path = '/data/local/tmp/_aw_smoke_test.bin'

  with tempfile.NamedTemporaryFile(delete=False, suffix='.bin') as tmp:
    tmp.write(payload)
    tmp_path = tmp.name

  try:
    appium_env.push_file(tmp_path, remote_path)
    pulled = appium_env.pull_file(remote_path)
    # Clean up remote file.
    try:
      appium_env.shell(f'rm -f "{remote_path}"')
    except Exception:  # pylint: disable=broad-except
      pass
  finally:
    os.unlink(tmp_path)

  if pulled != payload:
    return (
        False,
        f'Round-trip mismatch: pushed {len(payload)}B, pulled {len(pulled)}B',
    )
  return True, f'OK — {len(payload)} bytes round-tripped cleanly'


def _check_app_lifecycle(appium_env) -> tuple[bool, str]:
  """Check 5: launch, query state, and terminate a known package."""
  # Use the Settings app which is always present on AOSP.
  package = 'com.android.settings'

  # Launch via am start.
  try:
    appium_env.shell(f'am start -n {package}/.Settings')
  except Exception as exc:
    return False, f'Failed to launch {package}: {exc}'

  import time  # pylint: disable=g-import-not-at-top
  time.sleep(1.5)  # brief wait for the activity to start

  # Query app state — 4 means RUNNING in Appium's AppState enum.
  try:
    state = appium_env.query_app_state(package)
  except Exception as exc:
    return False, f'query_app_state raised: {exc}'

  if state not in (3, 4):  # 3=background, 4=foreground
    return False, f'Expected state 3 or 4 for {package}, got {state}'

  # Terminate.
  try:
    appium_env.terminate_app(package)
  except Exception as exc:
    return False, f'terminate_app raised: {exc}'

  return True, f'OK — {package} state={state}, launched and terminated'


def _run_checks(appium_env) -> int:
  """Run all 5 checks and return the number of failures."""
  checks = [
      ('Screenshot (numpy RGB)', _check_screenshot),
      ('Page-source (UI XML)', _check_page_source),
      ('Shell (mobile: shell)', _check_shell),
      ('File round-trip (push/pull)', _check_file_round_trip),
      ('App lifecycle (launch/terminate)', _check_app_lifecycle),
  ]

  failures = 0
  print()
  print('AgentRuntime Smoke Test')
  print('=' * 60)

  for name, fn in checks:
    print(f'\n  {name}')
    try:
      ok, msg = fn(appium_env)
    except Exception:  # pylint: disable=broad-except
      ok = False
      msg = traceback.format_exc()

    if ok:
      print(f'{_PASS} {msg}')
    else:
      print(f'{_FAIL} {msg}')
      failures += 1

  print()
  print('=' * 60)
  if failures == 0:
    print('All 5 checks passed.  Environment is ready for benchmarking.')
  else:
    print(
        f'{failures}/5 check(s) FAILED.  '
        'Fix the issues above before running the full benchmark.'
    )
  print()
  return failures


def _main(_argv) -> None:
  cfg = config_lib.AgentRuntimeConfig.from_flags()

  print(
      textwrap.dedent(f"""\
      AgentRuntime smoke test
      -----------------------
      domain  : {cfg.domain or "(not set)"}
      sandbox : {cfg.sandbox_id or "(will create from template: " + str(cfg.template) + ")"}
      """)
  )

  print('Connecting to sandbox and creating Appium controller…')
  try:
    controller = ac_lib.create_appium_controller(cfg)
  except Exception as exc:
    print(f'ERROR: Failed to create AppiumController: {exc}')
    traceback.print_exc()
    sys.exit(2)

  appium_env = controller._appium_env

  try:
    failures = _run_checks(appium_env)
  finally:
    print('Closing Appium driver and sandbox…')
    try:
      controller.close()
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning('controller.close() raised: %s', exc)

  sys.exit(1 if failures else 0)


if __name__ == '__main__':
  app.run(_main)
