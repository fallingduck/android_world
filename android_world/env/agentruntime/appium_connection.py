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

"""Isolated Appium WebDriver factory for the AgentRuntime backend.

Why isolation matters
---------------------
Each AgentRuntime sandbox has its own bearer token (``X-Access-Token``).  If
two sandboxes share the same Appium ``AppiumConnection`` class instance
(or worse, the same global ``extra_headers`` dict), token values bleed across
sandboxes and every request ends up authenticated with the wrong token.

This module solves the problem by *dynamically creating a per-sandbox
subclass* of ``AppiumConnection`` that captures the token at class-creation
time.  Because Appium's Python client reads ``extra_headers`` as a class
attribute, separate subclasses guarantee complete isolation.

Usage
-----
  session = SandboxSession.from_config(cfg)
  driver  = create_appium_driver(session)
  # ... use driver ...
  driver.quit()

Appium capabilities
-------------------
We target the UiAutomator2 driver which is the standard Android automation
driver used by android-sidecar.  Key capabilities:
  - ``platformName``     : Android
  - ``automationName``   : UiAutomator2
  - ``noReset``          : True  (don't reinstall apps or clear data between
                                  sessions; AndroidWorld manages its own state)
  - ``newCommandTimeout``: 300   (keep session alive during slow shell calls)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from absl import logging

if TYPE_CHECKING:
  from android_world.env.agentruntime.session import SandboxSession


# Appium default capabilities.
_PLATFORM_NAME = 'Android'
_AUTOMATION_NAME = 'UiAutomator2'
_NEW_COMMAND_TIMEOUT = 300  # seconds; keeps session alive during heavy calls

# Appium server connect / session-create timeout.
_DRIVER_CONNECT_TIMEOUT = 120  # seconds


def create_isolated_connection_class(access_token: str):
  """Return a *new* AppiumConnection subclass whose headers carry ``access_token``.

  Each call returns a distinct class object, so two calls with different tokens
  never share state.

  Args:
    access_token: The per-sandbox bearer token that must be sent as the
      ``X-Access-Token`` HTTP header on every Appium request.

  Returns:
    A subclass of ``appium.webdriver.appium_connection.AppiumConnection``
    with ``extra_headers`` frozen to ``{'X-Access-Token': access_token}``.

  Raises:
    ImportError: If the ``Appium-Python-Client`` package is not installed.
  """
  try:
    from appium.webdriver.appium_connection import AppiumConnection  # pylint: disable=g-import-not-at-top
  except ImportError as exc:
    raise ImportError(
        'The Appium-Python-Client package is required for AgentRuntime mode. '
        'Install it with: pip install Appium-Python-Client'
    ) from exc

  headers = {'X-Access-Token': access_token} if access_token else {}

  # Create an isolated subclass.  Using ``type()`` rather than a class
  # statement ensures every call produces a *new* class object even when
  # called with the same token (guarding against shared mutable class state).
  isolated_cls = type(
      f'_IsolatedAppiumConnection_{id(headers)}',
      (AppiumConnection,),
      {'extra_headers': headers},
  )
  return isolated_cls


def create_appium_driver(
    session: 'SandboxSession',
    *,
    new_command_timeout: int = _NEW_COMMAND_TIMEOUT,
    connect_timeout: int = _DRIVER_CONNECT_TIMEOUT,
):
  """Create a fully configured Appium WebDriver for ``session``.

  The driver is connected to the Appium server inside the sandbox, uses an
  isolated connection class so the token does not leak to other sandboxes, and
  sets conservative timeouts to survive slow ``mobile: shell`` calls.

  Args:
    session: A live ``SandboxSession`` containing the Appium URL and token.
    new_command_timeout: Appium ``newCommandTimeout`` capability in seconds.
      Controls how long Appium waits before killing the session due to
      inactivity.  Defaults to 300 s to handle heavy shell commands.
    connect_timeout: Timeout in seconds for the initial Appium session
      creation HTTP request.

  Returns:
    An ``appium.webdriver.Remote`` instance ready to use.

  Raises:
    ImportError: If the ``Appium-Python-Client`` package is not installed.
  """
  try:
    from appium import webdriver as appium_webdriver  # pylint: disable=g-import-not-at-top
    from appium.webdriver.client_config import AppiumClientConfig  # pylint: disable=g-import-not-at-top
    try:
      from appium.options.android import UiAutomator2Options  # pylint: disable=g-import-not-at-top
    except ImportError:
      from appium.options import UiAutomator2Options  # pylint: disable=g-import-not-at-top
  except ImportError as exc:
    raise ImportError(
        'The Appium-Python-Client package is required for AgentRuntime mode. '
        'Install it with: pip install Appium-Python-Client'
    ) from exc

  options = UiAutomator2Options()
  options.platform_name = _PLATFORM_NAME
  options.automation_name = _AUTOMATION_NAME
  options.no_reset = True
  options.new_command_timeout = new_command_timeout

  isolated_connection_cls = create_isolated_connection_class(
      session.access_token
  )

  logging.info(
      'Creating Appium driver (sandbox=%s, url=%s)…',
      session.sandbox_id,
      session.appium_url,
  )

  client_config = AppiumClientConfig(
      remote_server_addr=session.appium_url.replace('/wd/hub', ''),
      timeout=connect_timeout,
  )
  executor = isolated_connection_cls(client_config=client_config)

  driver = appium_webdriver.Remote(
      command_executor=executor,
      options=options,
  )

  logging.info(
      'Appium driver ready (sandbox=%s, session=%s).',
      session.sandbox_id,
      driver.session_id,
  )
  return driver
