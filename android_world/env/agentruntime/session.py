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

"""Sandbox session management for the AgentRuntime backend.

Responsibilities:
  - Create a new sandbox via the E2B SDK (``SandboxSession.create``).
  - Connect to an existing / already-running sandbox
    (``SandboxSession.connect``).
  - Expose the derived Appium endpoint URL and the per-sandbox bearer token
    needed to authenticate Appium requests.
  - Provide a lightweight health-check with configurable retries.
  - Tear down the sandbox on close (unless ``keep_sandbox=True``).

Design notes:
  - The E2B ``Sandbox`` object is kept as ``self._sandbox`` and is the single
    source of truth for ``sandbox_id`` and ``access_token``.
  - Port numbers follow the android-sidecar convention:
      APPIUM_PORT  = 4723  (Appium server)
      HEALTH_PORT  = 8080  (sidecar health endpoint)
      STREAM_PORT  = 8886  (ws-scrcpy / stream)
  - URLs are built with ``sandbox.get_host(port)`` which handles the
    ``<sandbox_id>-<port>.<domain>`` pattern used by E2B.
  - The class is intentionally NOT async; Appium WebDriver is also blocking
    I/O and the two live in the same ThreadPoolExecutor thread.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Optional

from absl import logging

from android_world.env.agentruntime import config as agentruntime_config


# Default port mapping used by android-sidecar inside the sandbox.
_APPIUM_PORT = 4723
_HEALTH_PORT = 8080
_STREAM_PORT = 8886

# Retry parameters for the health-check poll after sandbox creation.
_HEALTH_CHECK_RETRIES = 15
_HEALTH_CHECK_SLEEP_SECS = 2.0


@dataclasses.dataclass
class SandboxSession:
  """Holds all connection metadata for one AgentRuntime Android sandbox.

  Attributes:
    sandbox_id: Unique sandbox identifier assigned by E2B / AgentRuntime.
    access_token: Bearer token used to authenticate Appium requests.  Must be
      set as the ``X-Access-Token`` header on every Appium connection for this
      sandbox.
    appium_url: Full HTTP URL of the Appium server inside the sandbox, e.g.
      ``https://<sandbox_id>-4723.<domain>/wd/hub``.
    health_url: Full HTTP URL of the sidecar health endpoint.
    stream_url: WebSocket URL for the ws-scrcpy stream (optional use).
    _sandbox: The underlying E2B ``Sandbox`` object.  Private; callers should
      use the public attributes above.
    _keep: Whether to keep the sandbox alive when ``close()`` is called.
  """

  sandbox_id: str
  access_token: str
  appium_url: str
  health_url: str
  stream_url: str
  _sandbox: object  # e2b.Sandbox — typed as object to avoid hard import at top
  _keep: bool = False

  # ------------------------------------------------------------------
  # Factory class-methods
  # ------------------------------------------------------------------

  @classmethod
  def create(
      cls,
      cfg: agentruntime_config.AgentRuntimeConfig,
  ) -> 'SandboxSession':
    """Create a brand-new sandbox and wait until Appium is healthy.

    Args:
      cfg: Validated ``AgentRuntimeConfig``.  ``cfg.template`` is used as the
        E2B template name; ``cfg.timeout`` is passed as the sandbox TTL.

    Returns:
      A ready-to-use ``SandboxSession``.

    Raises:
      ImportError: If the ``e2b`` package is not installed.
      RuntimeError: If the Appium health check does not pass within the retry
        budget.
    """
    try:
      import e2b  # pylint: disable=g-import-not-at-top
    except ImportError as exc:
      raise ImportError(
          'The e2b package is required for AgentRuntime mode.  '
          'Install it with: pip install e2b'
      ) from exc

    logging.info(
        'Creating AgentRuntime sandbox (template=%s, timeout=%ds)…',
        cfg.template,
        cfg.timeout,
    )
    sandbox = e2b.Sandbox.create(
        template=cfg.template,
        timeout=cfg.timeout,
        api_key=cfg.api_key,
        domain=cfg.domain,
    )
    session = cls._from_sandbox(sandbox, keep=cfg.keep_sandbox)
    logging.info(
        'Sandbox created: id=%s  appium=%s',
        session.sandbox_id,
        session.appium_url,
    )
    session._wait_for_appium()
    return session

  @classmethod
  def connect(
      cls,
      cfg: agentruntime_config.AgentRuntimeConfig,
  ) -> 'SandboxSession':
    """Connect to an existing / running sandbox.

    Args:
      cfg: Validated ``AgentRuntimeConfig``.  ``cfg.sandbox_id`` identifies
        the sandbox to connect to.

    Returns:
      A ready-to-use ``SandboxSession``.

    Raises:
      ImportError: If the ``e2b`` package is not installed.
      RuntimeError: If the Appium health check does not pass.
    """
    try:
      import e2b  # pylint: disable=g-import-not-at-top
    except ImportError as exc:
      raise ImportError(
          'The e2b package is required for AgentRuntime mode.  '
          'Install it with: pip install e2b'
      ) from exc

    logging.info(
        'Connecting to existing sandbox id=%s…', cfg.sandbox_id
    )
    sandbox = e2b.Sandbox._cls_connect(
        sandbox_id=cfg.sandbox_id,
        api_key=cfg.api_key,
        domain=cfg.domain,
    )
    session = cls._from_sandbox(sandbox, keep=cfg.keep_sandbox)
    logging.info(
        'Connected to sandbox: id=%s  appium=%s',
        session.sandbox_id,
        session.appium_url,
    )
    session._wait_for_appium()
    return session

  @classmethod
  def from_config(
      cls,
      cfg: agentruntime_config.AgentRuntimeConfig,
  ) -> 'SandboxSession':
    """Create or connect depending on whether ``cfg.sandbox_id`` is set.

    This is the primary factory to use from ``appium_controller.py`` and
    ``env_launcher.py``.

    Args:
      cfg: Validated ``AgentRuntimeConfig``.

    Returns:
      A ready-to-use ``SandboxSession``.
    """
    cfg.validate()
    if cfg.sandbox_id:
      return cls.connect(cfg)
    return cls.create(cfg)

  # ------------------------------------------------------------------
  # Internal helpers
  # ------------------------------------------------------------------

  @classmethod
  def _from_sandbox(cls, sandbox, *, keep: bool) -> 'SandboxSession':
    """Build a SandboxSession from a live E2B Sandbox object."""
    sandbox_id = sandbox.sandbox_id

    # The per-sandbox access token is stored in the private attribute that the
    # E2B SDK itself uses to set X-Access-Token headers.
    # Always use _envd_access_token — it authenticates both the envd sidecar
    # and the Appium port on TencentAGR deployments.  traffic_access_token is
    # a separate routing credential and must NOT be used here.
    access_token: str = sandbox._envd_access_token or ''

    appium_host = sandbox.get_host(_APPIUM_PORT)
    health_host = sandbox.get_host(_HEALTH_PORT)
    stream_host = sandbox.get_host(_STREAM_PORT)

    return cls(
        sandbox_id=sandbox_id,
        access_token=access_token,
        appium_url=f'https://{appium_host}/wd/hub',
        health_url=f'https://{health_host}/health',
        stream_url=f'wss://{stream_host}',
        _sandbox=sandbox,
        _keep=keep,
    )

  def _wait_for_appium(
      self,
      retries: int = _HEALTH_CHECK_RETRIES,
      sleep_secs: float = _HEALTH_CHECK_SLEEP_SECS,
  ) -> None:
    """Poll the Appium health endpoint until it responds 200.

    Args:
      retries: Maximum number of attempts.
      sleep_secs: Seconds to sleep between attempts.

    Raises:
      RuntimeError: If Appium never becomes healthy within the budget.
    """
    try:
      import httpx  # pylint: disable=g-import-not-at-top
    except ImportError as exc:
      raise ImportError(
          'The httpx package is required for AgentRuntime mode.  '
          'Install it with: pip install httpx'
      ) from exc

    headers = {}
    if self.access_token:
      headers['X-Access-Token'] = self.access_token

    for attempt in range(1, retries + 1):
      try:
        resp = httpx.get(
            self.appium_url.replace('/wd/hub', '/status'),
            headers=headers,
            timeout=5.0,
        )
        if resp.status_code == 200:
          logging.info(
              'Appium is healthy (sandbox=%s, attempt=%d/%d).',
              self.sandbox_id,
              attempt,
              retries,
          )
          return
        logging.warning(
            'Appium health check returned %d (sandbox=%s, attempt=%d/%d).',
            resp.status_code,
            self.sandbox_id,
            attempt,
            retries,
        )
      except Exception as exc:  # pylint: disable=broad-except
        logging.warning(
            'Appium health check failed (sandbox=%s, attempt=%d/%d): %s',
            self.sandbox_id,
            attempt,
            retries,
            exc,
        )
      if attempt < retries:
        time.sleep(sleep_secs)

    raise RuntimeError(
        f'Appium server at {self.appium_url} did not become healthy after'
        f' {retries} attempts (sandbox={self.sandbox_id}).'
    )

  # ------------------------------------------------------------------
  # Lifecycle
  # ------------------------------------------------------------------

  def close(self) -> None:
    """Optionally kill the sandbox and release resources.

    If ``_keep=True``, the sandbox is left running so that it can be
    re-connected later (useful for debugging).
    """
    if self._keep:
      logging.info(
          'Keeping sandbox alive (id=%s) as requested.', self.sandbox_id
      )
      return
    try:
      self._sandbox.kill()
      logging.info('Killed sandbox id=%s.', self.sandbox_id)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'Failed to kill sandbox id=%s: %s', self.sandbox_id, exc
      )

  def __repr__(self) -> str:
    return (
        f'SandboxSession(id={self.sandbox_id!r},'
        f' appium={self.appium_url!r},'
        f' keep={self._keep})'
    )
