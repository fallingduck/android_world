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

"""Configuration for the AgentRuntime / E2B + Appium backend.

Usage:
  # From absl CLI flags (run.py / minimal_task_runner.py):
  config = AgentRuntimeConfig.from_flags()

  # From environment variables / .env file (matches mobile-use convention):
  config = AgentRuntimeConfig.from_env()

  # From a sandboxes.yaml file (connect to pre-created sandbox pool):
  config = AgentRuntimeConfig.from_sandboxes_yaml('sandboxes.yaml')

  # Direct construction:
  config = AgentRuntimeConfig(domain=..., api_key=..., ...)

Environment variable names follow the **mobile-use** convention used by
``ags-cookbook/examples/mobile-use/.env``.  The legacy ``AGENTRUNTIME_*``
prefix is also accepted as a fallback for backwards compatibility.

Primary variables (mobile-use standard):
  E2B_API_KEY          AgentSandbox / E2B API key
  E2B_DOMAIN           Service domain, e.g. ap-chongqing.tencentags.com
  SANDBOX_TEMPLATE     Sandbox template name, e.g. android_world_test
  SANDBOX_TIMEOUT      Sandbox TTL in seconds (default: 300)
  SANDBOX_COUNT        Number of sandboxes for parallel mode (default: 1)
  THREAD_POOL_SIZE     Worker thread pool size (default: 4)
  PROCESS_COUNT        OS-process count (default: 1)
  EXECUTION_MODE       'serial' or 'parallel' (default: 'serial')
  BATCH_SLEEP_INTERVAL Sleep between batch rounds in seconds (default: 0)

Sandboxes YAML (pre-created pool):
  When ``sandbox_ids_file`` points to a YAML file with a ``sandbox_ids`` list,
  the SandboxPool will *connect* to those existing sandboxes instead of
  *creating* new ones.  Example file::

    sandbox_ids:
      - sdx-abc123
      - sdx-def456

Design notes:
  - All fields are optional at construction time.  Validation is deferred to
    the moment a sandbox session is actually created.
  - Execution-mode and concurrency parameters live in the same config so that
    a single CLI flag set drives both single-sandbox and parallel-runner paths.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Literal

from absl import flags

FLAGS = flags.FLAGS

# ---------------------------------------------------------------------------
# Execution mode literal type
# ---------------------------------------------------------------------------
ExecutionMode = Literal['serial', 'parallel']


@dataclasses.dataclass
class AgentRuntimeConfig:
  """Unified configuration for the AgentRuntime Appium backend.

  Attributes:
    domain: The AgentSandbox service domain, e.g. ``ap-chongqing.tencentags.com``.
    api_key: API key (``E2B_API_KEY``) for the AgentSandbox service.
    sandbox_id: ID of a *single* existing sandbox to connect to.  Used in
      serial mode when you want to reuse a pre-created sandbox.
    template: Sandbox template name used when *creating* new sandboxes.
    timeout: Sandbox TTL in seconds (used during creation).
    keep_sandbox: Whether to keep sandboxes alive after the run (for debugging).
    appium_shell_timeout: Timeout for ``mobile: shell`` calls (seconds).
    execution_mode: ``'serial'`` or ``'parallel'``.
    sandbox_count: How many sandboxes to allocate in parallel mode.  If
      ``sandbox_ids_file`` is set this is ignored and the YAML list length is
      used instead.
    process_count: OS-process count for the multi-process runner.
    thread_pool_size: ThreadPoolExecutor size driving Appium I/O.
    max_concurrency: Upper bound on simultaneously-running tasks.  ``None``
      defaults to ``sandbox_count``.
    dump_logcat_on_failure: Capture logcat / screenshot bundle on task failure.
    dump_artifacts_dir: Root directory for per-sandbox artefact output.
    sandbox_ids_file: Path to a YAML file containing a ``sandbox_ids`` list.
      When set, the pool connects to those existing sandboxes rather than
      creating new ones.  Matches the ``sandboxes.yaml`` convention used by
      ``ags-cookbook/examples/mobile-use/batch_dump_logcat.py``.
  """

  domain: str | None = None
  api_key: str | None = None
  sandbox_id: str | None = None
  template: str | None = None
  timeout: int = 300
  keep_sandbox: bool = False

  # Appium-specific timeouts
  appium_shell_timeout: int = 30

  # Session reconnection
  appium_reconnect_retries: int = 2
  """Max reconnection attempts per task when InvalidSessionIdException occurs."""

  # Concurrency / execution
  execution_mode: ExecutionMode = 'serial'
  sandbox_count: int = 1
  process_count: int = 1
  thread_pool_size: int = 4
  max_concurrency: int | None = None

  # Diagnostics / artifacts
  dump_logcat_on_failure: bool = True
  dump_artifacts_dir: str | None = None

  # Pre-created sandbox pool (sandboxes.yaml)
  sandbox_ids_file: str | None = None

  # -------------------------------------------------------------------------
  # Computed property: resolved sandbox id list (from yaml or single id)
  # -------------------------------------------------------------------------

  def get_sandbox_ids(self) -> list[str]:
    """Return the list of sandbox IDs to use, loading from YAML if configured.

    Returns:
      A list of sandbox ID strings.  Empty list means "create new sandboxes"
      (using ``template``).

    Raises:
      FileNotFoundError: If ``sandbox_ids_file`` is set but does not exist.
      ValueError: If the YAML file does not contain a ``sandbox_ids`` key or
        the list is empty.
    """
    if self.sandbox_ids_file:
      return _load_sandbox_ids_yaml(self.sandbox_ids_file)
    if self.sandbox_id:
      return [self.sandbox_id]
    return []

  # -------------------------------------------------------------------------
  # Factory helpers
  # -------------------------------------------------------------------------

  @classmethod
  def from_flags(cls) -> 'AgentRuntimeConfig':
    """Build config from absl FLAGS, falling back to env vars for unset flags.

    Strategy: start from ``from_env()`` so that .env / os.environ values are
    always the baseline, then let any explicitly-passed CLI flag override the
    corresponding field.  This ensures that values set in .env are visible even
    when the caller did not pass the corresponding --flag on the command line.
    """
    cfg = cls.from_env()

    # Override with explicit CLI flags where the user actually passed a value.
    if FLAGS.agentruntime_domain:
      cfg.domain = FLAGS.agentruntime_domain
    if FLAGS.agentruntime_api_key:
      cfg.api_key = FLAGS.agentruntime_api_key
    if FLAGS.agentruntime_sandbox_id:
      cfg.sandbox_id = FLAGS.agentruntime_sandbox_id
    if FLAGS.agentruntime_template:
      cfg.template = FLAGS.agentruntime_template
    if FLAGS.agentruntime_timeout != 300:  # non-default
      cfg.timeout = FLAGS.agentruntime_timeout
    if FLAGS.agentruntime_keep_sandbox:
      cfg.keep_sandbox = FLAGS.agentruntime_keep_sandbox
    if FLAGS.agentruntime_shell_timeout != 30:  # non-default
      cfg.appium_shell_timeout = FLAGS.agentruntime_shell_timeout
    if FLAGS.execution_mode != 'serial':
      cfg.execution_mode = FLAGS.execution_mode
    if FLAGS.sandbox_count != 1:
      cfg.sandbox_count = FLAGS.sandbox_count
    if FLAGS.process_count != 1:
      cfg.process_count = FLAGS.process_count
    if FLAGS.thread_pool_size != 4:
      cfg.thread_pool_size = FLAGS.thread_pool_size
    if FLAGS.max_concurrency:
      cfg.max_concurrency = FLAGS.max_concurrency
    if FLAGS.dump_logcat_on_failure:
      cfg.dump_logcat_on_failure = FLAGS.dump_logcat_on_failure
    if FLAGS.dump_artifacts_dir:
      cfg.dump_artifacts_dir = FLAGS.dump_artifacts_dir
    if FLAGS.sandboxes_yaml:
      cfg.sandbox_ids_file = FLAGS.sandboxes_yaml
      # Sandboxes listed in a yaml file are pre-created by the user and must
      # never be killed by this process.
      cfg.keep_sandbox = True

    return cfg

  @classmethod
  def from_env(cls) -> 'AgentRuntimeConfig':
    """Build config from environment variables.

    Follows the **mobile-use** convention (``E2B_*`` / ``SANDBOX_*`` prefix).
    Legacy ``AGENTRUNTIME_*`` names are accepted as fallbacks so that existing
    deployments don't break.

    Recognised environment variables (mobile-use names first, fallback in
    parentheses):
      E2B_DOMAIN        (AGENTRUNTIME_DOMAIN)
      E2B_API_KEY       (AGENTRUNTIME_API_KEY)
      SANDBOX_TEMPLATE  (AGENTRUNTIME_TEMPLATE)
      SANDBOX_TIMEOUT   (AGENTRUNTIME_TIMEOUT)      default 300
      SANDBOX_COUNT                                 default 1
      THREAD_POOL_SIZE  (AGENTRUNTIME_SHELL_TIMEOUT for shell only)
      PROCESS_COUNT                                 default 1
      EXECUTION_MODE                                default 'serial'
      BATCH_SLEEP_INTERVAL                          (unused by runner, kept for parity)
      DUMP_LOGCAT_ON_FAILURE                        default true
      DUMP_ARTIFACTS_DIR
      SANDBOXES_YAML                                path to sandboxes.yaml
    """
    def _get(*keys: str, default: str = '') -> str:
      """Return the value of the first matching env var, or default."""
      for k in keys:
        v = os.environ.get(k, '')
        if v:
          return v
      return default

    def _bool(*keys: str, default: bool) -> bool:
      val = _get(*keys).lower()
      if val in ('1', 'true', 'yes'):
        return True
      if val in ('0', 'false', 'no'):
        return False
      return default

    def _int(*keys: str, default: int) -> int:
      val = _get(*keys)
      try:
        return int(val)
      except (ValueError, TypeError):
        return default

    return cls(
        domain=_get('E2B_DOMAIN', 'AGENTRUNTIME_DOMAIN') or None,
        api_key=_get('E2B_API_KEY', 'AGENTRUNTIME_API_KEY') or None,
        sandbox_id=_get('AGENTRUNTIME_SANDBOX_ID') or None,
        template=_get('SANDBOX_TEMPLATE', 'AGENTRUNTIME_TEMPLATE') or None,
        timeout=_int('SANDBOX_TIMEOUT', 'AGENTRUNTIME_TIMEOUT', default=300),
        keep_sandbox=_bool('AGENTRUNTIME_KEEP_SANDBOX', default=False),
        appium_shell_timeout=_int('AGENTRUNTIME_SHELL_TIMEOUT', default=30),
        execution_mode=_get('EXECUTION_MODE', default='serial'),  # type: ignore[arg-type]
        sandbox_count=_int('SANDBOX_COUNT', default=1),
        process_count=_int('PROCESS_COUNT', default=1),
        thread_pool_size=_int('THREAD_POOL_SIZE', default=4),
        max_concurrency=_int('MAX_CONCURRENCY', default=0) or None,
        dump_logcat_on_failure=_bool('DUMP_LOGCAT_ON_FAILURE', default=True),
        dump_artifacts_dir=_get('DUMP_ARTIFACTS_DIR') or None,
        sandbox_ids_file=_get('SANDBOXES_YAML') or None,
    )

  @classmethod
  def from_sandboxes_yaml(
      cls,
      yaml_path: str,
      *,
      domain: str | None = None,
      api_key: str | None = None,
      **kwargs,
  ) -> 'AgentRuntimeConfig':
    """Build a parallel-mode config that connects to sandboxes listed in a YAML file.

    This is a convenience factory for the ``sandboxes.yaml`` pattern used in
    ``ags-cookbook/examples/mobile-use/``.  It loads the YAML, sets
    ``execution_mode='parallel'``, and derives ``sandbox_count`` from the list
    length.

    Example YAML::

      sandbox_ids:
        - sdx-abc123
        - sdx-def456

    Args:
      yaml_path: Path to the sandboxes YAML file.
      domain: AGS domain (falls back to ``E2B_DOMAIN`` env var).
      api_key: AGS API key (falls back to ``E2B_API_KEY`` env var).
      **kwargs: Any other ``AgentRuntimeConfig`` fields to override.

    Returns:
      A ready-to-validate ``AgentRuntimeConfig`` in parallel mode.
    """
    sandbox_ids = _load_sandbox_ids_yaml(yaml_path)
    base = cls.from_env()
    # Override with explicit params.
    if domain:
      base.domain = domain
    if api_key:
      base.api_key = api_key
    base.sandbox_ids_file = yaml_path
    base.execution_mode = 'parallel'
    base.sandbox_count = len(sandbox_ids)
    base.keep_sandbox = True  # Don't kill sandboxes we didn't create.
    for k, v in kwargs.items():
      setattr(base, k, v)
    return base

  def validate(self) -> None:
    """Raise ValueError if required fields are missing.

    Call this just before actually creating or connecting sandbox sessions.
    """
    if not self.domain:
      raise ValueError(
          'AgentRuntimeConfig.domain is required.  '
          'Set --agentruntime_domain, E2B_DOMAIN env var, or add it to .env.'
      )
    if not self.api_key:
      raise ValueError(
          'AgentRuntimeConfig.api_key is required.  '
          'Set --agentruntime_api_key, E2B_API_KEY env var, or add it to .env.'
      )
    # sandbox_ids_file satisfies the "how to find sandboxes" requirement.
    has_source = self.sandbox_id or self.template or self.sandbox_ids_file
    if not has_source:
      raise ValueError(
          'One of sandbox_id, template, or sandbox_ids_file must be set.  '
          'Use SANDBOX_TEMPLATE (or --agentruntime_template) to create new '
          'sandboxes, or SANDBOXES_YAML (or --sandboxes_yaml) to connect to '
          'existing ones.'
      )
    if self.execution_mode not in ('serial', 'parallel'):
      raise ValueError(
          f"execution_mode must be 'serial' or 'parallel', got: {self.execution_mode!r}"
      )
    if self.sandbox_count < 1:
      raise ValueError('sandbox_count must be >= 1.')
    if self.process_count < 1:
      raise ValueError('process_count must be >= 1.')
    if self.thread_pool_size < 1:
      raise ValueError('thread_pool_size must be >= 1.')


# ---------------------------------------------------------------------------
# YAML loader helper
# ---------------------------------------------------------------------------

def _load_sandbox_ids_yaml(yaml_path: str) -> list[str]:
  """Load and validate a ``sandboxes.yaml`` file.

  Expected format::

    sandbox_ids:
      - sdx-abc123
      - sdx-def456

  Args:
    yaml_path: Path to the YAML file.

  Returns:
    List of sandbox ID strings (guaranteed non-empty).

  Raises:
    FileNotFoundError: If the file does not exist.
    ValueError: If the file is malformed or the list is empty.
  """
  try:
    import yaml  # pylint: disable=g-import-not-at-top
  except ImportError as exc:
    raise ImportError(
        'PyYAML is required to load sandboxes.yaml.  '
        'Install it with: pip install PyYAML'
    ) from exc

  if not os.path.isfile(yaml_path):
    raise FileNotFoundError(
        f'sandboxes.yaml not found: {yaml_path!r}'
    )

  with open(yaml_path, 'r', encoding='utf-8') as fh:
    data = yaml.safe_load(fh)

  if not isinstance(data, dict) or 'sandbox_ids' not in data:
    raise ValueError(
        f'{yaml_path!r} must contain a top-level "sandbox_ids" key.  '
        f'Got: {data!r}'
    )

  ids = data['sandbox_ids']
  if not isinstance(ids, list) or len(ids) == 0:
    raise ValueError(
        f'"sandbox_ids" in {yaml_path!r} must be a non-empty list.  '
        f'Got: {ids!r}'
    )

  # Filter placeholder values (the default template contains '- xxx').
  real_ids = [str(sid).strip() for sid in ids if str(sid).strip() not in ('', 'xxx', 'null', 'None')]
  if not real_ids:
    raise ValueError(
        f'"sandbox_ids" in {yaml_path!r} contains only placeholder values.  '
        'Replace them with real sandbox IDs.'
    )

  return real_ids


# ---------------------------------------------------------------------------
# absl flags registration
# ---------------------------------------------------------------------------

def register_flags() -> None:
  """Register all AgentRuntime-related absl flags.

  Call this once at module level in run.py / minimal_task_runner.py **before**
  ``app.run()``.  Safe to call multiple times (subsequent calls are no-ops).
  """
  _safe_define(
      flags.DEFINE_string,
      'agentruntime_domain',
      None,
      'AgentSandbox service domain, e.g. ap-chongqing.tencentags.com.  '
      'Can also be set via the E2B_DOMAIN environment variable.',
  )
  _safe_define(
      flags.DEFINE_string,
      'agentruntime_api_key',
      None,
      'AgentSandbox API key.  Can also be set via E2B_API_KEY env var.',
  )
  _safe_define(
      flags.DEFINE_string,
      'agentruntime_sandbox_id',
      None,
      'Connect to a single existing sandbox with this ID.',
  )
  _safe_define(
      flags.DEFINE_string,
      'agentruntime_template',
      None,
      'Sandbox template name to use when creating new sandboxes.  '
      'Can also be set via SANDBOX_TEMPLATE env var.',
  )
  _safe_define(
      flags.DEFINE_integer,
      'agentruntime_timeout',
      300,
      'Sandbox session timeout in seconds.  '
      'Can also be set via SANDBOX_TIMEOUT env var.',
  )
  _safe_define(
      flags.DEFINE_boolean,
      'agentruntime_keep_sandbox',
      False,
      'Keep sandboxes alive after the run (useful for debugging).',
  )
  _safe_define(
      flags.DEFINE_integer,
      'agentruntime_shell_timeout',
      30,
      'Timeout in seconds for mobile:shell calls through the Appium bridge.',
  )
  _safe_define(
      flags.DEFINE_enum,
      'execution_mode',
      'serial',
      ['serial', 'parallel'],
      "Execution mode: 'serial' (single env) or 'parallel' (sandbox pool).",
  )
  _safe_define(
      flags.DEFINE_integer,
      'sandbox_count',
      1,
      'Number of sandboxes to allocate in parallel execution mode.  '
      'Overridden by the length of --sandboxes_yaml when that flag is set.',
  )
  _safe_define(
      flags.DEFINE_integer,
      'process_count',
      1,
      'Number of OS processes for the multi-process parallel runner.',
  )
  _safe_define(
      flags.DEFINE_integer,
      'thread_pool_size',
      4,
      'Size of the ThreadPoolExecutor driving blocking Appium driver calls.  '
      'Can also be set via THREAD_POOL_SIZE env var.',
  )
  _safe_define(
      flags.DEFINE_integer,
      'max_concurrency',
      0,
      'Max simultaneous tasks (0 = use sandbox_count).',
  )
  _safe_define(
      flags.DEFINE_boolean,
      'dump_logcat_on_failure',
      True,
      'Capture logcat / screenshot bundle when a task fails.',
  )
  _safe_define(
      flags.DEFINE_string,
      'dump_artifacts_dir',
      None,
      'Root directory for per-sandbox artefacts (screenshots, logcat, etc.).',
  )
  _safe_define(
      flags.DEFINE_string,
      'sandboxes_yaml',
      None,
      'Path to a sandboxes.yaml file listing pre-created sandbox IDs to '
      'connect to.  When set, the pool connects to those sandboxes instead '
      'of creating new ones.  Implies --execution_mode=parallel.  '
      'Can also be set via SANDBOXES_YAML env var.',
  )


def _safe_define(define_fn, name: str, *args, **kwargs) -> None:
  """Call ``define_fn(name, *args, **kwargs)`` unless the flag already exists."""
  try:
    define_fn(name, *args, **kwargs)
  except flags.DuplicateFlagError:
    pass
