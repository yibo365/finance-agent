from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _run_dev_script(tmp_path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    state_dir = tmp_path / "state"
    fake_bin.mkdir()
    state_dir.mkdir()
    log_path = tmp_path / "commands.log"

    _write_executable(
        fake_bin / "lsof",
        """
        #!/usr/bin/env bash
        echo "lsof $*" >> "$DEV_TEST_LOG"
        count_file="$DEV_TEST_STATE/lsof_count"
        count="$(cat "$count_file" 2>/dev/null || echo 0)"
        count=$((count + 1))
        echo "$count" > "$count_file"
        if [ "$DEV_TEST_MODE" = "occupied" ] && [[ "$*" == *"tcp:8765"* ]] && [ "$count" -eq 1 ]; then
          echo 43210
          exit 0
        fi
        exit 1
        """,
    )
    _write_executable(
        fake_bin / "ps",
        """
        #!/usr/bin/env bash
        echo "/usr/bin/python unrelated-service --port 8765"
        """,
    )
    _write_executable(
        fake_bin / "curl",
        """
        #!/usr/bin/env bash
        echo "curl $*" >> "$DEV_TEST_LOG"
        count_file="$DEV_TEST_STATE/curl_count"
        count="$(cat "$count_file" 2>/dev/null || echo 0)"
        count=$((count + 1))
        echo "$count" > "$count_file"
        if [ "$DEV_TEST_MODE" = "wait" ] && [ "$count" -lt 3 ]; then
          exit 7
        fi
        echo '{}'
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "uv",
        """
        #!/usr/bin/env bash
        echo "uv $*" >> "$DEV_TEST_LOG"
        while [ ! -f "$DEV_TEST_STATE/npm_started" ]; do
          sleep 0.05
        done
        exit 0
        """,
    )
    _write_executable(
        fake_bin / "npm",
        """
        #!/usr/bin/env bash
        echo "npm $*" >> "$DEV_TEST_LOG"
        if [[ "$*" == *" run dev "* ]]; then
          touch "$DEV_TEST_STATE/npm_started"
        fi
        exit 0
        """,
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "DEV_TEST_LOG": str(log_path),
            "DEV_TEST_MODE": mode,
            "DEV_TEST_STATE": str(state_dir),
        }
    )
    result = subprocess.run(
        [str(PROJECT_ROOT / "scripts/dev.sh")],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )
    result.command_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    return result


def test_dev_script_waits_for_backend_before_starting_vite(tmp_path: Path) -> None:
    result = _run_dev_script(tmp_path, "wait")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "curl -fsS http://127.0.0.1:8765/api/state" in result.command_log
    assert result.command_log.index("curl ") < result.command_log.index(
        "npm --prefix webapp run dev"
    )


def test_dev_script_restarts_when_backend_port_is_occupied(tmp_path: Path) -> None:
    result = _run_dev_script(tmp_path, "occupied")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "后端 端口 8765 被占用" in result.stdout
    assert "npm --prefix webapp run dev" in result.command_log
