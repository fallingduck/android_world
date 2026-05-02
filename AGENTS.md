# Repository Guidelines

## Project Structure & Module Organization
`android_world/` contains the main Python package: `env/` for emulator and device control, `agents/` for agent implementations, `task_evals/` for benchmark tasks and validators, and `utils/` for shared helpers. Root entry points include `run.py` for benchmark runs, `minimal_task_runner.py` for quick checks, and `server/android_server.py` for the FastAPI service. Android app code and Robolectric tests live under `apps/java/` and `apps/javatests/`. Supporting docs are in `docs/`, container scripts in `docker_setup/`, and reference media in `assets/`.

## Build, Test, and Development Commands
Use Python 3.11+.

- `pip install -r requirements.txt && python setup.py install`: install runtime dependencies and generate protobuf stubs during setup.
- `python minimal_task_runner.py --task=ContactsAddContact`: smoke-test the environment against one task.
- `python run.py --suite_family=android_world --agent_name=t3a_gpt4 --perform_emulator_setup`: run the main benchmark; keep `--perform_emulator_setup` for first-time device setup.
- `pytest -vv -n auto --ignore=android_env android_world`: run the Python test suite in the same shape as CI.
- `bazel test //...`: run Android/Kotlin targets under `apps/` when changing MiniWoB or app-side code.

## Coding Style & Naming Conventions
Follow the Google Python style reflected in [`.pylintrc`](/Users/zhangyuehua/Documents/code/android_world/.pylintrc): 2-space indentation in Python, `snake_case` for modules/functions/variables, `PascalCase` for classes, and descriptive constant names. Keep new task classes aligned with existing naming such as `ContactsAddContact` and pair tests with `_test.py` suffixes. For Kotlin, match the existing `PascalCase` type names and Bazel target layout. Prefer small, focused modules over large multi-purpose files.

## Testing Guidelines
Place Python tests alongside the code they cover using `*_test.py` names, for example `android_world/env/adb_utils_test.py`. Add or update tests for any validator, agent, or environment change; emulator-facing logic should have at least one deterministic unit test where possible. When editing task definitions, run the nearest package tests first, then a targeted smoke run with `minimal_task_runner.py`.

## Commit & Pull Request Guidelines
Recent history follows short Conventional Commit prefixes such as `feat(env): ...`, `fix(device_constants): ...`, `build(proto): ...`, and `chore: ...`. Keep commits scoped and imperative. Pull requests should describe the behavioral change, list validation commands you ran, link the relevant issue or task, and include screenshots or logs when UI, emulator setup, or server behavior changes. If protobuf outputs change, mention whether they were regenerated from `setup.py`.

## Environment & Configuration Tips
Local runs depend on an Android emulator or redroid backend plus API keys such as `OPENAI_API_KEY` and optionally `GCP_API_KEY`. Do not commit secrets, local runtime notes, or emulator-specific artifacts. Keep generated protobuf files and task metadata in sync with code changes.
