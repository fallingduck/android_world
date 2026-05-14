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

"""Constants for the Pixel 6, API 33, emulator."""

# =============================================================================
# 【修改说明】修正 Markor 数据目录大小写。
# 修改日期：2026-05-14
#
# 修改内容：
#   MARKOR_DATA 从 "/storage/emulated/0/Documents/Markor"（大写 M）改为
#   "/storage/emulated/0/Documents/markor"（小写 m）。
#
# 修改原因：
#   Markor v0.146 在 AOSP/redroid 上创建的数据目录为小写 markor，
#   而上游 AndroidWorld 硬编码了大写 Markor（Google 签名版的行为）。
#   redroid 上用大写路径会找不到目录，导致所有 Markor 任务误判为失败。
#
# 对评测语义/难度的影响：无。
#   - 仅修正文件路径使其匹配实际文件系统状态，不改变任务目标、
#     评估标准或 agent 交互流程。若路径错误则任务必然失败，
#     修正后才能正确评估。
# =============================================================================

import datetime

# Screen dimensions of Pixel 6.
SCREEN_HEIGHT, SCREEN_WIDTH = 2400, 1080

# Where data on emulator is stored.
EMULATOR_DATA = "/storage/emulated/0/"

# Location where app snapshots are stored.
SNAPSHOT_DATA = "/data/data/android_world/snapshots"

# keep-sorted start
AUDIORECORDER_DATA = "/storage/emulated/0/Android/data/com.dimowner.audiorecorder/files/Music/records"
DOWNLOAD_DATA = "/storage/emulated/0/Download"
GALLERY_DATA = "/sdcard/DCIM"
# NOTE: Markor v0.146 on AOSP / redroid creates its data directory with a
# lowercase name (``markor``), whereas upstream AndroidWorld historically
# assumed the Google-signed Markor build uses ``Markor``. We default to the
# lowercase form for the redroid backend. If running against a Google
# Emulator image with the capitalized folder, override this constant or
# (TODO: Phase 2) add runtime detection via ``file_utils`` at task init.
MARKOR_DATA = "/storage/emulated/0/Documents/markor"
MUSIC_DATA = "/sdcard/Music"
OSMAND_DATA = "/storage/emulated/0/Android/data/net.osmand/files"
PHOTOS_DATA = "/sdcard/Pictures"
VIDEOS_DATA = "/sdcard/Movies"
# keep-sorted end

# Every task starts October 15, 2023 @ 15:34:00.
TIMEZONE = "UTC"
DT = datetime.datetime(2023, 10, 15, 15, 34, 0, tzinfo=datetime.timezone.utc)

# Format the datetime object into the Android date-time format
ANDROID_DT = DT.strftime("%m%d%H%M%y.%S")
