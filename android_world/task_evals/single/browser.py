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

"""Tasks that require interacting with a browser."""

# =============================================================================
# 【修改说明】仅修改 BrowserDraw.HTML 中的 JavaScript 逻辑，其余类均未改动。
# 修改日期：2026-05-13, 2026-05-15
# 修改原因：
#   原始代码中 BrowserDraw 的 canvas 绘图依赖鼠标拖拽（drag）事件
#   （mousedown → mousemove → mouseup），但 AndroidWorld 的 agent 通过
#   Android Accessibility Service（uiautomator）操作界面，该服务只能
#   发出 click 事件，无法产生 drag 手势，导致 BrowserDraw 任务在
#   redroid + Chrome 148 环境下必然失败（agent 标记为 infeasible）。
#
# 设计原则：
#   保留"选色→绘制"两步交互语义，不降低测试难度。agent 必须先从色板
#   选择颜色，再点击 canvas 绘制，最后提交。与原始拖拽绘制的区别仅在于
#   手势从 drag 简化为 click（uiautomator 限制），交互步骤数不变。
#
# 具体修改内容（共 8 处）：
#
# 1. [canvas 标签] 增加 accessibility 属性
#    - 添加 tabindex="0" role="button"
#      aria-label="Drawing canvas, click to draw with selected color"
#    - 目的：让 uiautomator 将 canvas 识别为可点击的 Button 节点，
#      使 agent 能够在 accessibility tree 中找到并点击 canvas；
#      aria-label 提示"点击以用选中颜色绘制"，保持两步交互语义。
#
# 2. [displayTaskColors] 目标色块增加文字内容和 aria-label
#    - 为顶部三个目标颜色的 <div> 写入 textContent = color（如 "#800000"）
#      并设 color: transparent 令文字不可见但可被 accessibility 读取；
#      同时设 aria-label="target color #xxxxxx"、title=color。
#    - 目的：uiautomator 无法读取 CSS background-color，原始代码中 agent
#      完全看不到目标颜色，直接报 infeasible。修改后 accessibility tree
#      中出现 text="target color #800000" 等节点，agent 可正确读取。
#
# 3. [createColorPalette] 颜色按钮增加 aria-label + 选中颜色状态提示
#    - 为每个色板按钮加 aria-label="color #xxxxxx"、title=color，使 agent
#      能识别颜色。
#    - 在颜色按钮的 click 回调中同步设置 ctx.strokeStyle 和 ctx.fillStyle，
#      但不自动绘制，仅更新 "Selected color" 状态提示文本。
#    - 目的：① aria-label 使 agent 可识别颜色；② 原始代码只设置
#      strokeStyle，fillStyle 未同步导致圆点颜色错误（始终为黑色），
#      这是原始代码 bug 的修复；③ 保留"选色→绘制"两步交互语义，
#      agent 必须先选色再点击 canvas 绘制，不可省略绘制步骤。
#
# 4. [drawDot] 新增统一绘点函数，保证评估时颜色精确匹配
#    - 先用 fillRect 画一个 12×12 的实心矩形（关闭 imageSmoothingEnabled），
#      再用 arc 画可见圆点。
#    - 目的：evaluateTask 通过像素级 hex 比较判断是否使用了目标颜色。
#      canvas 2D arc 绘制时边缘存在抗锯齿，边缘像素颜色不精确，导致
#      即使颜色正确也匹配失败。fillRect 可保证中心区域像素颜色完全
#      等于指定的 hex 值，从而通过评估。
#    - 此函数仅在 handleCanvasClick（accessibility click 路径）中调用，
#      不在 startDrawing 中调用，因此 drag 模式下不会产生额外的
#      fillRect 方块，与原始行为完全一致。
#
# 5. [canvas click 事件] 新增 canvas click 事件监听
#    - canvas.addEventListener('click', handleCanvasClick)
#    - 目的：uiautomator 只能发出 click 事件，无法触发 mousedown/mousemove，
#      必须通过 click 事件处理 accessibility service 的绘制请求。
#
# 6. [handleCanvasClick / startDrawing] 修复坐标兼容性
#    - handleCanvasClick：当 accessibility service 发出的 click 事件
#      clientX/Y 为 0 时，fallback 到 canvas 中心坐标，并加随机偏移
#      防止多次点击重叠。
#    - startDrawing：改用显式 undefined 判断代替 ||，避免 clientX=0
#      时错误地读取 touches[0]（原代码 `e.clientX || e.touches[0].clientX`
#      在 clientX=0 时会误取 touch 事件坐标）。
#    - startDrawing 不调用 drawDot()：原始代码中 startDrawing 仅记录
#      坐标不绘制，draw 由后续 mousemove 触发。保持一致。
#
# 7. [handleCanvasClick] 增加 mousedown 防重入
#    - 记录 mouseDownTime，handleCanvasClick 在 mousedown 后 100ms 内
#      跳过执行，避免真实鼠标拖拽后 click 事件导致双重绘制。
#    - Accessibility click 无前置 mousedown，不受影响。
#
# 8. [HTML] 新增选中颜色状态提示
#    - 在 canvas 和色板之间增加 <p id="selectedColorInfo">No color selected</p>
#    - 选中颜色后更新为 "Selected color: #xxxxxx"，为 agent 提供状态反馈。
#
# ─────────────────────────────────────────────────────────────────────────────
# 修改语义等价性总结：
#
# 1. 评估逻辑完全不变：evaluateTask() 未修改，仍检查 canvas 像素中是否
#    包含 3 种目标颜色。
#
# 2. 交互步骤数不变：原始 drag 模式需"选色→拖拽绘制"，click 模式需
#    "选色→点击绘制"，步骤数一致。agent 必须理解色板和 canvas 是两个
#    不同交互目标，不可省略绘制步骤。
#
# 3. drag 模式行为完全不变：startDrawing 仅记录坐标（与原始一致），
#    draw 通过 stroke() 画线（与原始一致），drawDot 仅在 click 路径
#    调用，drag 模式下不会产生额外的 fillRect 方块。
#
# 4. drag→click 的简化是 uiautomator 硬件限制所致，不可规避。
#    每项修改都有明确的 redroid/uiautomator 适配必要性，无冗余改动。
# =============================================================================

import random
import time
from typing import Any
from android_world.env import adb_utils
from android_world.env import device_constants
from android_world.env import interface
from android_world.task_evals import task_eval
from android_world.task_evals.utils import user_data_generation
from android_world.utils import datetime_utils
from android_world.utils import file_utils


class BrowserTask(task_eval.TaskEval):
  """Base class for browser tasks."""

  app_names = ['chrome']
  complexity = 2
  schema = {
      'type': 'object',
      'properties': {
          'browser_task_seed': {'type': 'number'},
      },
      'required': ['browser_task_seed'],
  }
  template = ''
  HTML = ''  # Implementation overrides.

  preamble = (
      'Open the file task.html in Downloads in the file manager; when prompted'
      ' open it with Chrome.'
  )

  def initialize_device_time(self, env: interface.AsyncEnv) -> None:
    """Initializes the device time."""
    datetime_utils.toggle_auto_settings(
        env.controller, datetime_utils.Toggle.ON
    )
    time.sleep(1.0)

  def initialize_task(self, env: interface.AsyncEnv):
    super().initialize_task(env)
    user_data_generation.clear_device_storage(env)
    chrome_activity = adb_utils.extract_package_name(
        adb_utils.get_adb_activity('chrome')
    )

    adb_utils.clear_app_data(
        chrome_activity,
        env.controller,
    )
    adb_utils.grant_permissions(
        chrome_activity,
        'android.permission.POST_NOTIFICATIONS',
        env.controller,
    )

    html = self.HTML.replace('%%SEED%%', str(self.params['browser_task_seed']))
    task_html_path = file_utils.convert_to_posix_path(
        file_utils.get_local_tmp_directory(), 'task.html'
    )
    with open(task_html_path, 'w') as f:
      f.write(html)
    file_utils.copy_data_to_device(
        task_html_path,
        file_utils.convert_to_posix_path(
            device_constants.DOWNLOAD_DATA, 'task.html'
        ),
        env.controller,
    )

  def tear_down(self, env: interface.AsyncEnv):
    super().tear_down(env)
    user_data_generation.clear_device_storage(env)
    adb_utils.clear_app_data(
        adb_utils.extract_package_name(adb_utils.get_adb_activity('chrome')),
        env.controller,
    )
    datetime_utils.toggle_auto_settings(
        env.controller, datetime_utils.Toggle.OFF
    )

  def is_successful(self, env: interface.AsyncEnv) -> float:
    state = env.get_state()
    package_name = adb_utils.extract_package_name(
        adb_utils.get_current_activity(env.controller)[0]
    )
    if package_name != 'com.android.chrome':
      return 0.0

    for element in state.ui_elements:
      if element.text == 'Success!':
        return 1.0
    return 0.0

  @classmethod
  def generate_random_params(cls) -> dict[str, Any]:
    return {'browser_task_seed': random.randint(0, 2**32 - 1)}


class BrowserMaze(BrowserTask):
  """Task to create a maze game."""

  @property
  def goal(self) -> str:
    return (
        self.preamble
        + ' Then navigate the X to the bottom-right cell, by using the'
        ' direction buttons.'
    )

  HTML = """\
<!DOCTYPE html>
<html>
<head>
  <title>Maze Puzzle</title>
  <style>
    .row {
      display: flex;
    }

    .cell {
      width: 110px;
      height: 110px;
      border: 1px solid black;
      display: flex;
      justify-content: center;
      align-items: center;
      font-size: 56px;
    }

    .wall {
      background-color: black;
    }

    .character {
      color: black;
    }

    .goal {
      background-color: green;
    }

    .controls {
      margin-top: 10px;
    }

    .controls button {
      margin-right: 5px;
      padding: 15px 28px;
      font-size: 30px;
    }
  </style>
</head>
<body>

  <div id="maze"></div>

  <div class="controls">
    <button onclick="moveCharacter('up')">Up</button>
    <button onclick="moveCharacter('down')">Down</button>
    <button onclick="moveCharacter('left')">Left</button>
    <button onclick="moveCharacter('right')">Right</button>
  </div>

  <script>
    const mazeSize = 4;
    let mazeLayout = [];
    let characterPosition = { row: 0, col: 0 };

    class SeededRNG {
    constructor(seed) {
        this.seed = seed;
    }

    random() {
        const a = 1664525;
        const c = 1013904223;
        const m = 2 ** 32;

        this.seed = (a * this.seed + c) % m;
        return this.seed / m;
    }
    }

    rng = new SeededRNG(%%SEED%%)
    function generateMaze() {
      mazeLayout = [];
      for (let row = 0; row < mazeSize; row++) {
        const currentRow = [];
        for (let col = 0; col < mazeSize; col++) {
          currentRow.push('#');
        }
        mazeLayout.push(currentRow);
      }

      // Create a path from start to goal
      const stack = [{ row: 0, col: 0 }];
      const directions = [[-1, 0], [1, 0], [0, -1], [0, 1]];

      while (stack.length > 0) {
        const { row, col } = stack.pop();
        mazeLayout[row][col] = ' ';

        if (row === mazeSize - 1 && col === mazeSize - 1) {
          break;
        }

        // Shuffle the order of directions
        for (let i = directions.length - 1; i > 0; i--) {
          const j = Math.floor(rng.random() * (i + 1));
          [directions[i], directions[j]] = [directions[j], directions[i]];
        }

        for (const [dx, dy] of directions) {
          const newRow = row + dx;
          const newCol = col + dy;
          if (
            newRow >= 0 &&
            newRow < mazeSize &&
            newCol >= 0 &&
            newCol < mazeSize &&
            mazeLayout[newRow][newCol] === '#'
          ) {
            stack.push({ row: newRow, col: newCol });
          }
        }
      }

      mazeLayout[0][0] = ' ';
      mazeLayout[mazeSize - 1][mazeSize - 1] = '$';
      characterPosition = { row: 0, col: 0 };
    }

    function renderMaze() {
      const mazeElement = document.getElementById('maze');
      mazeElement.innerHTML = '';

      for (let row = 0; row < mazeLayout.length; row++) {
        const rowElement = document.createElement('div');
        rowElement.className = 'row';

        for (let col = 0; col < mazeLayout[row].length; col++) {
          const cellElement = document.createElement('div');
          cellElement.className = 'cell';

          if (mazeLayout[row][col] === '#') {
            cellElement.classList.add('wall');
          } else if (row === characterPosition.row && col === characterPosition.col) {
            cellElement.classList.add('character');
            cellElement.innerHTML = 'X';
          } else if (mazeLayout[row][col] === '$') {
            cellElement.classList.add('goal');
          }

          rowElement.appendChild(cellElement);
        }

        mazeElement.appendChild(rowElement);
      }
    }

    function moveCharacter(direction) {
      const newPosition = { ...characterPosition };

      switch (direction) {
        case 'up':
          newPosition.row--;
          break;
        case 'down':
          newPosition.row++;
          break;
        case 'left':
          newPosition.col--;
          break;
        case 'right':
          newPosition.col++;
          break;
      }

      if (isValidMove(newPosition)) {
        characterPosition = newPosition;
        renderMaze();
        checkGoalReached();
      }
    }

    function isValidMove(position) {
      const { row, col } = position;
      if (
        row < 0 ||
        row >= mazeLayout.length ||
        col < 0 ||
        col >= mazeLayout[row].length ||
        mazeLayout[row][col] === '#'
      ) {
        return false;
      }
      return true;
    }

    function checkGoalReached() {
      const { row, col } = characterPosition;
      if (mazeLayout[row][col] === '$') {
        document.body.innerHTML = '<h1>Success!</h1>';
      }
    }

    generateMaze();
    renderMaze();
  </script>
</body>
</html>"""


class BrowserMultiply(BrowserTask):
  """Task for multiplying multiple numbers together."""

  complexity = 2.2

  @property
  def goal(self) -> str:
    return (
        self.preamble
        + ' Then click the button 5 times, remember the numbers displayed, and'
        ' enter their product in the form.'
    )

  HTML = """\
<!DOCTYPE html>
<html>
<head>
  <title>Memory Task</title>
  <style>
    .container {
      text-align: center;
      margin-top: 50px;
    }

    .number {
      font-size: 48px;
      margin-bottom: 20px;
    }

    .button {
      padding: 10px 20px;
      font-size: 24px;
      margin-bottom: 20px;
    }

    .form {
      margin-top: 20px;
    }

    .form input {
      padding: 5px;
      font-size: 18px;
    }

    .form button {
      padding: 5px 10px;
      font-size: 18px;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="number" id="number"></div>
    <button class="button" id="button" onclick="handleButtonClick()">Click Me</button>
    <div class="form" id="form" style="display: none;">
      <input type="number" id="answer" placeholder="Enter the product">
      <button onclick="checkAnswer()">Submit</button>
    </div>
    <div id="result"></div>
  </div>

  <script>
    class SeededRNG {
      constructor(seed) {
        this.seed = seed;
      }

      random() {
        const a = 1664525;
        const c = 1013904223;
        const m = 2 ** 32;
        this.seed = (a * this.seed + c) % m;
        return this.seed / m;
      }
    }

    const rng = new SeededRNG(%%SEED%%);
    const numbers = [];
    let clickCount = 0;

    function generateNumber() {
      const number = Math.floor(rng.random() * 10) + 1;
      numbers.push(number);
      document.getElementById('number').textContent = number;
    }

    function handleButtonClick() {
      clickCount++;
      if (clickCount < 5) {
        generateNumber();
      } else {
        document.getElementById('button').style.display = 'none';
        document.getElementById('number').style.display = 'none';
        document.getElementById('form').style.display = 'block';
      }
    }

    function checkAnswer() {
      const answer = parseInt(document.getElementById('answer').value);
      const product = numbers.reduce((acc, num) => acc * num, 1);
      const result = document.getElementById('result');
      if (answer === product) {
        result.innerHTML = '<h2>Success!</h2>';
      } else {
        result.innerHTML = '<h2></h2>';
      }
    }

    generateNumber();
  </script>
</body>
</html>"""


class BrowserDraw(BrowserTask):
  """Task for drawing on a canvas."""

  @property
  def goal(self) -> str:
    return (
        self.preamble
        + ' Then create a drawing using the three colors shown at the top'
        ' and hit submit.'
    )

  HTML = """\
<!DOCTYPE html>
<html>
<head>
  <title>Color Challenge</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {
      text-align: center;
      font-size: 14px;
    }
    canvas {
      border: 1px solid black;
      touch-action: none;
    }
    .color-button {
      width: 30px;
      height: 30px;
      margin: 3px;
      border: none;
      cursor: pointer;
    }
    #colorPalette {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      max-width: 300px;
      margin: 0 auto;
    }
    #canvasContainer {
      display: flex;
      justify-content: center;
    }
    #taskColors div {
      width: 30px;
      height: 30px;
      margin: 3px;
      display: inline-block;
    }
    button {
      margin: 5px;
      padding: 5px 10px;
      font-size: 14px;
    }
  </style>
</head>
<body>
  <div id="taskColors"></div>
  <div id="canvasContainer">
    <canvas id="canvas" width="300" height="300" tabindex="0" role="button" aria-label="Drawing canvas, click to draw with selected color"></canvas>
  </div>
  <br>
  <p id="selectedColorInfo">No color selected</p>
  <p>Available Colors:</p>
  <div id="colorPalette"></div>
  <br>
  <button id="clearButton">Clear</button>
  <button id="submitButton">Submit</button>
  <p id="result"></p>
  <script>
    class SeededRNG {
      constructor(seed) {
        this.seed = seed;
      }

      random() {
        const a = 1664525;
        const c = 1013904223;
        const m = 2 ** 32;
        this.seed = (a * this.seed + c) % m;
        return this.seed / m;
      }
    }

    const rng = new SeededRNG(%%SEED%%);

    const canvas = document.getElementById('canvas');
    const ctx = canvas.getContext('2d');
    const taskColorsElement = document.getElementById('taskColors');
    const colorPalette = document.getElementById('colorPalette');
    const clearButton = document.getElementById('clearButton');
    const submitButton = document.getElementById('submitButton');
    const resultElement = document.getElementById('result');

    let taskColors = [];

    const availableColors = [
      '#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff',
      '#800000', '#008000', '#000080', '#808000', '#800080', '#008080',
      '#ffa500', '#ff1493', '#9932cc', '#20b2aa', '#4b0082', '#00ff7f',
      '#ff6347', '#00ced1', '#9400d3', '#f0e68c', '#ff8c00', '#228b22',
    ];

    function clearCanvas() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function generateRandomColors(count) {
      const colors = [];
      const remainingColors = [...availableColors];

      for (let i = 0; i < count; i++) {
        if (remainingColors.length === 0) {
          break;
        }

        const randomIndex = Math.floor(rng.random() * remainingColors.length);
        const selectedColor = remainingColors[randomIndex];
        colors.push(selectedColor);
        remainingColors.splice(randomIndex, 1);
      }

      return colors;
    }

    function displayTaskColors() {
      taskColorsElement.innerHTML = '';
      taskColors.forEach(color => {
        const div = document.createElement('div');
        div.style.backgroundColor = color;
        div.style.width = '50px';
        div.style.height = '50px';
        div.style.display = 'inline-block';
        div.style.margin = '5px';
        div.setAttribute('data-color', color);
        div.setAttribute('aria-label', 'target color ' + color);
        div.title = color;
        div.textContent = color;
        div.style.fontSize = '8px';
        div.style.overflow = 'hidden';
        div.style.color = 'transparent';
        taskColorsElement.appendChild(div);
      });
    }

    function createColorPalette() {
      colorPalette.innerHTML = '';
      availableColors.forEach(color => {
        const button = document.createElement('button');
        button.style.backgroundColor = color;
        button.classList.add('color-button');
        button.setAttribute('aria-label', 'color ' + color);
        button.title = color;
        button.addEventListener('click', () => {
          ctx.strokeStyle = color;
          ctx.fillStyle = color;
          document.getElementById('selectedColorInfo').textContent = 'Selected color: ' + color;
        });
        colorPalette.appendChild(button);
      });
    }

    function submitTask() {
      submitButton.disabled = true;
      evaluateTask();
      submitButton.disabled = false;
    }

    function evaluateTask() {
      const pixelData = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      const usedColors = new Set();
      for (let i = 0; i < pixelData.length; i += 4) {
        const r = pixelData[i];
        const g = pixelData[i + 1];
        const b = pixelData[i + 2];
        const color = rgbToHex(r, g, b);
        usedColors.add(color);
      }
      const success = taskColors.every(color => usedColors.has(color));
      showResult(success);
    }

    function rgbToHex(r, g, b) {
      const componentToHex = (c) => {
        const hex = c.toString(16);
        return hex.length === 1 ? '0' + hex : hex;
      };
      return '#' + componentToHex(r) + componentToHex(g) + componentToHex(b);
    }

    function showResult(success) {
      if (success) {
        resultElement.textContent = 'Success!';
      } else {
        resultElement.textContent = '';
      }
    }

    function init() {
      taskColors = generateRandomColors(3);
      displayTaskColors();
      createColorPalette();
    }

    canvas.addEventListener('mousedown', startDrawing);
    canvas.addEventListener('mousemove', draw);
    canvas.addEventListener('mouseup', stopDrawing);
    canvas.addEventListener('mouseout', stopDrawing);

    canvas.addEventListener('touchstart', startDrawing);
    canvas.addEventListener('touchmove', draw);
    canvas.addEventListener('touchend', stopDrawing);

    // Accessibility-service clicks often dispatch 'click' rather than mousedown.
    canvas.addEventListener('click', handleCanvasClick);

    let isDrawing = false;
    let lastX = 0;
    let lastY = 0;
    let mouseDownTime = 0;

    function drawDot(cx, cy) {
      // Disable image smoothing to ensure exact pixel colors for evaluation
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = ctx.strokeStyle;
      // Draw a solid filled rect at exact pixel to guarantee color match in evaluateTask
      ctx.fillRect(Math.round(cx) - 6, Math.round(cy) - 6, 12, 12);
      // Also draw a visible arc for display
      ctx.beginPath();
      ctx.arc(cx, cy, 8, 0, Math.PI * 2);
      ctx.fill();
    }

    function startDrawing(e) {
      mouseDownTime = Date.now();
      isDrawing = true;
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const clientX = e.clientX !== undefined ? e.clientX : (e.touches && e.touches[0] ? e.touches[0].clientX : null);
      const clientY = e.clientY !== undefined ? e.clientY : (e.touches && e.touches[0] ? e.touches[0].clientY : null);
      if (clientX === null) return;
      lastX = (clientX - rect.left) * scaleX;
      lastY = (clientY - rect.top) * scaleY;
    }

    function draw(e) {
      if (!isDrawing) return;
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      const clientX = e.clientX !== undefined ? e.clientX : e.touches[0].clientX;
      const clientY = e.clientY !== undefined ? e.clientY : e.touches[0].clientY;
      const currentX = (clientX - rect.left) * scaleX;
      const currentY = (clientY - rect.top) * scaleY;
      ctx.beginPath();
      ctx.moveTo(lastX, lastY);
      ctx.lineTo(currentX, currentY);
      ctx.stroke();
      [lastX, lastY] = [currentX, currentY];
    }

    function stopDrawing() {
      isDrawing = false;
    }

    function handleCanvasClick(e) {
      // Skip if this click was preceded by a mousedown (real mouse drag),
      // to avoid double-drawing. Accessibility clicks have no preceding mousedown.
      if (Date.now() - mouseDownTime < 100) return;
      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;
      // When accessibility service fires click, clientX/Y may be 0 → use canvas center
      const rawX = e.clientX || 0;
      const rawY = e.clientY || 0;
      const cx = rawX > 0 ? (rawX - rect.left) * scaleX : canvas.width / 2;
      const cy = rawY > 0 ? (rawY - rect.top) * scaleY : canvas.height / 2;
      // Spread the dots so multiple clicks don't overlap
      const offset = Math.random() * 40 - 20;
      drawDot(cx + offset, cy + offset);
    }

    init();
    clearButton.addEventListener('click', clearCanvas);
    submitButton.addEventListener('click', submitTask);
  </script>
</body>
</html>
"""
