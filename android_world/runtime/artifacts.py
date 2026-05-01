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

"""Per-sandbox artefact writer for the AgentRuntime parallel runner.

Each sandbox that runs a task gets its own output directory:

  <root>/
    sandbox_0/
      task_ContactsAddContact_seed30/
        result.json
        last_screenshot.png
        page_source.xml
        logcat.txt
        console.log
    sandbox_1/
      ...

``ArtifactWriter`` is created by ``SandboxPool`` and attached to each
``SandboxHandle``.  The parallel runner calls its helpers at task boundaries
and on failure.

All writes are best-effort: any exception is caught and logged rather than
re-raised, so a failed artefact write never aborts a task run.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
from typing import Any, Optional

from absl import logging
import numpy as np


class ArtifactWriter:
  """Writes diagnostic artefacts for one sandbox.

  Args:
    output_dir: Root directory for this sandbox's artefacts.  If ``None``,
      a temporary directory is created on first use.
    sandbox_id: Sandbox identifier used in log messages.
  """

  def __init__(
      self,
      output_dir: Optional[str] = None,
      sandbox_id: str = '',
  ):
    self._output_dir = output_dir
    self._sandbox_id = sandbox_id
    self._task_dir: Optional[str] = None

  # -------------------------------------------------------------------------
  # Task boundary helpers
  # -------------------------------------------------------------------------

  def begin_task(self, task_name: str, seed: int) -> None:
    """Set up the artefact directory for a new task run."""
    root = self._ensure_root()
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dir_name = f'task_{task_name}_seed{seed}_{ts}'
    self._task_dir = os.path.join(root, dir_name)
    os.makedirs(self._task_dir, exist_ok=True)
    logging.info(
        'ArtifactWriter: artefacts → %s (sandbox=%s)',
        self._task_dir,
        self._sandbox_id,
    )

  def end_task(self) -> None:
    """Reset the task-level directory pointer."""
    self._task_dir = None

  # -------------------------------------------------------------------------
  # Write helpers
  # -------------------------------------------------------------------------

  def write_screenshot(self, pixels: np.ndarray) -> None:
    """Save an RGB numpy screenshot as PNG."""
    path = self._task_path('last_screenshot.png')
    if path is None:
      return
    try:
      import cv2  # pylint: disable=g-import-not-at-top
      bgr = cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR)
      cv2.imwrite(path, bgr)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'ArtifactWriter: screenshot write failed (sandbox=%s): %s',
          self._sandbox_id,
          exc,
      )

  def write_page_source(self, xml: str) -> None:
    """Save the current uiautomator XML dump."""
    self._write_text('page_source.xml', xml)

  def write_logcat(self, logcat: str) -> None:
    """Save device logcat output."""
    self._write_text('logcat.txt', logcat)

  def write_console(self, text: str) -> None:
    """Append a line to the console log."""
    path = self._task_path('console.log')
    if path is None:
      return
    try:
      with open(path, 'a', encoding='utf-8') as f:
        f.write(text)
        if not text.endswith('\n'):
          f.write('\n')
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'ArtifactWriter: console log write failed (sandbox=%s): %s',
          self._sandbox_id,
          exc,
      )

  def write_result(self, result: dict[str, Any]) -> None:
    """Write the task result as JSON."""
    path = self._task_path('result.json')
    if path is None:
      return
    try:
      with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'ArtifactWriter: result write failed (sandbox=%s): %s',
          self._sandbox_id,
          exc,
      )

  def write_failure_bundle(
      self,
      pixels: Optional[np.ndarray] = None,
      xml: Optional[str] = None,
      logcat: Optional[str] = None,
      error: Optional[str] = None,
  ) -> None:
    """Write all available diagnostic artefacts on task failure."""
    if pixels is not None:
      self.write_screenshot(pixels)
    if xml is not None:
      self.write_page_source(xml)
    if logcat is not None:
      self.write_logcat(logcat)
    if error:
      self.write_console(f'ERROR: {error}\n')

  # -------------------------------------------------------------------------
  # Internal helpers
  # -------------------------------------------------------------------------

  def _ensure_root(self) -> str:
    if self._output_dir is None:
      self._output_dir = tempfile.mkdtemp(prefix='aw_artefacts_')
      logging.info(
          'ArtifactWriter: created temp root %s (sandbox=%s)',
          self._output_dir,
          self._sandbox_id,
      )
    os.makedirs(self._output_dir, exist_ok=True)
    return self._output_dir

  def _task_path(self, filename: str) -> Optional[str]:
    """Return the full path for ``filename`` inside the current task dir."""
    if self._task_dir is None:
      # No task started; write to sandbox root.
      try:
        root = self._ensure_root()
        return os.path.join(root, filename)
      except Exception:  # pylint: disable=broad-except
        return None
    return os.path.join(self._task_dir, filename)

  def _write_text(self, filename: str, content: str) -> None:
    path = self._task_path(filename)
    if path is None:
      return
    try:
      with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    except Exception as exc:  # pylint: disable=broad-except
      logging.warning(
          'ArtifactWriter: write %s failed (sandbox=%s): %s',
          filename,
          self._sandbox_id,
          exc,
      )

  def __repr__(self) -> str:
    return (
        f'ArtifactWriter(sandbox={self._sandbox_id!r},'
        f' dir={self._output_dir!r})'
    )
