"""The scripts that run the ProofAgent evaluation on a laptop: a command that does not exit cannot hold up what follows.

On 14 November a series of three live runs stood still for almost three hours. The first run was complete and
uploaded; the upload command had printed its last line and its process stayed. These tests run the real scripts
with a stand-in for ``voiceshop`` (tests/fake_voiceshop.py) that can be told to stay, at each step.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("proofagent_harness")  # the run script refuses to start without it

REPO = Path(__file__).resolve().parents[1]
BASH = os.environ.get("BASH_UNDER_TEST", "bash")  # macOS ships bash 3.2; point this at one to test with it
pytestmark = pytest.mark.skipif(shutil.which(BASH) is None, reason="no bash")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A folder with the scripts, an empty virtual environment to source, the keys the run script asks for, and the stand-in."""
    (tmp_path / "scripts").mkdir()
    for name in ("_proofagent_lib.sh", "run_proofagent.sh", "run_proofagent_series.sh"):
        shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=x\nSERPER_API_KEY=x\n", encoding="utf-8")
    stand_in = tmp_path / "voiceshop"
    stand_in.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{REPO / "tests" / "fake_voiceshop.py"}" "$@"\n', encoding="utf-8")
    stand_in.chmod(0o755)
    return tmp_path


def environment(project: Path, plan: str = "", **more: str) -> dict[str, str]:
    return {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}", "VOICESHOP": str(project / "voiceshop"),
            "FAKE_PLAN": plan, "FAKE_CALLS": str(project / "calls.txt"), "PROOFAGENT_API_KEY": "a-key-for-the-test",
            "GUARD_POLL": "0.2", "GUARD_SETTLE": "0", "GUARD_KILL_AFTER": "2", "RUN_GRACE": "1", "RUN_LIMIT": "4", "UPLOAD_GRACE": "1", "UPLOAD_LIMIT": "4",
            "SERIES_GRACE": "1", "SERIES_LIMIT": "4", **more}


def series(project: Path, *args: str, plan: str = "", **more: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([BASH, str(project / "scripts" / "run_proofagent_series.sh"), *args], capture_output=True, text=True, timeout=120,
                          env=environment(project, plan, **more), cwd=project.parent)


def calls(project: Path) -> list[str]:
    return [line.split(" ", 1)[1] for line in (project / "calls.txt").read_text(encoding="utf-8").splitlines()]


def pid_of(project: Path, step: str) -> int:
    return next(int(line.split(" ", 1)[0]) for line in (project / "calls.txt").read_text(encoding="utf-8").splitlines() if line.endswith(" " + step))


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a process that has exited and has not been collected still answers; its state says so
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def test_a_series_runs_every_run_uploads_it_and_reads_the_runs_together(project: Path) -> None:
    done = series(project, "t", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls(project) == ["run:t-1", "upload:t-1", "run:t-2", "upload:t-2", "series"]
    assert "# ProofAgent scorecard: t-1" in done.stdout and "Uploaded run 't-2'" in done.stdout  # what each step printed is shown
    assert "SERIES COMPLETE: 2 of 2 runs. Read: eval/results/proofagent/series-t-1.md" in done.stdout
    assert "did not exit" not in done.stdout and "Terminated" not in done.stderr
    assert "Uploaded run 't-1'" in (project / "logs" / "proofagent_t-1.log").read_text(encoding="utf-8")


def test_an_upload_that_is_complete_and_does_not_exit_is_stopped_and_the_next_run_starts(project: Path) -> None:
    """14 November: the receipt was written, the last line printed, and the process stayed for hours."""
    started = time.time()
    done = series(project, "t", "2", plan="upload:t-1=stay_after_results")
    assert done.returncode == 0, done.stdout + done.stderr
    assert calls(project) == ["run:t-1", "upload:t-1", "run:t-2", "upload:t-2", "series"]
    assert "Uploaded run 't-1'" in done.stdout
    assert "Note: the upload was complete and its process did not exit; it was stopped. The run is on the dashboard." in done.stdout
    assert not is_running(pid_of(project, "upload:t-1"))
    assert time.time() - started < 30
    assert "Terminated" not in done.stderr and "Killed" not in done.stderr


def test_a_run_that_wrote_its_results_and_does_not_exit_counts_as_finished(project: Path) -> None:
    done = series(project, "t", "2", plan="run:t-1=stay_after_results")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Note: the run had written its results and its process did not exit; it was stopped. The results are complete." in done.stdout
    assert calls(project) == ["run:t-1", "upload:t-1", "run:t-2", "upload:t-2", "series"]
    assert "SERIES COMPLETE: 2 of 2 runs" in done.stdout
    assert not is_running(pid_of(project, "run:t-1"))


def test_a_run_without_a_result_is_stopped_at_its_limit_and_the_series_goes_on(project: Path) -> None:
    done = series(project, "t", "3", plan="run:t-1=stay")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PROOFAGENT RUN STOPPED: no result after" in done.stdout
    assert "Run t-1 did not finish; the series goes on." in done.stdout
    assert calls(project) == ["run:t-1", "run:t-2", "upload:t-2", "run:t-3", "upload:t-3", "series"]  # nothing to upload for the first
    assert "SERIES COMPLETE: 2 of 3 runs" in done.stdout and "Did not finish: t-1" in done.stdout
    assert not is_running(pid_of(project, "run:t-1"))


def test_a_run_that_fails_is_reported_as_failed_and_is_not_uploaded(project: Path) -> None:
    done = series(project, "t", "2", plan="run:t-2=fail")
    assert done.returncode == 1
    assert "run:t-2: it went wrong" in done.stdout and "PROOFAGENT RUN FAILED" in done.stdout
    assert calls(project) == ["run:t-1", "upload:t-1", "run:t-2"]
    assert "SERIES NOT COMPLETE: 1 of 2 runs finished (t-2 failed)" in done.stdout


def test_an_upload_that_never_answers_is_stopped_and_the_run_still_counts(project: Path) -> None:
    done = series(project, "t", "2", plan="upload:t-1=stay")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "The upload did not finish in" in done.stdout and "Try again with: voiceshop eval proofagent --label t-1 --upload" in done.stdout
    assert "SERIES COMPLETE: 2 of 2 runs" in done.stdout


def test_a_command_that_does_not_react_to_being_asked_to_stop_is_killed(project: Path) -> None:
    done = series(project, "t", "2", plan="upload:t-1=stay_after_results_ignoring_term")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "SERIES COMPLETE: 2 of 2 runs" in done.stdout
    assert not is_running(pid_of(project, "upload:t-1"))


def test_the_step_that_reads_the_runs_together_cannot_hold_up_the_end_either(project: Path) -> None:
    done = series(project, "t", "2", plan="series=stay_after_results")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "# One version, run 2 times" in done.stdout and "SERIES COMPLETE: 2 of 2 runs" in done.stdout


def test_a_series_started_again_keeps_its_finished_runs_and_does_the_rest(project: Path) -> None:
    """After an interruption the same command is given again: no finished run is paid for twice."""
    first = series(project, "t", "3", plan="run:t-2=fail,run:t-3=fail")
    assert first.returncode == 1 and (project / "eval/results/proofagent/t-1/scorecard.json").exists()
    (project / "eval/results/proofagent/t-1/upload.json").unlink()  # as if the first attempt had been stopped before the upload
    (project / "calls.txt").unlink()
    again = series(project, "t", "3")
    assert again.returncode == 0, again.stdout + again.stderr
    assert calls(project) == ["upload:t-1", "run:t-2", "upload:t-2", "run:t-3", "upload:t-3", "series"]
    assert "Already finished, and kept: Behavioural score (E) 95% SILVER (commit abc1234f)" in again.stdout
    assert "SERIES COMPLETE: 3 of 3 runs" in again.stdout
    (project / "calls.txt").unlink()
    third = series(project, "t", "3")  # everything is there: nothing runs but the reading
    assert calls(project) == ["series"] and third.stdout.count("Already finished, and kept") == 3


def test_a_kept_run_of_another_commit_is_pointed_out(project: Path) -> None:
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(project)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "one"], check=True)
    here = subprocess.run([*git, "rev-parse", "--short=8", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    series(project, "t", "2", FAKE_VERSION="0000000f")
    again = series(project, "t", "2")
    assert "Note: that run is of commit 0000000f and this folder is at " + here[:7] in again.stdout
    shutil.rmtree(project / "eval")
    series(project, "t", "2", FAKE_VERSION=here)  # the scorecard holds eight characters of the commit, git gives seven
    same = series(project, "t", "2")
    assert "Already finished, and kept" in same.stdout and "Runs of two versions" not in same.stdout


def test_an_interrupt_stops_the_run_in_progress_and_the_series_with_it(project: Path) -> None:
    """Ctrl-C goes to the scripts and to nothing they started in the background; they have to pass it on."""
    running = subprocess.Popen([BASH, str(project / "scripts" / "run_proofagent_series.sh"), "t", "3"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=environment(project, "run:t-1=stay", RUN_LIMIT="60"), cwd=project.parent, start_new_session=True)
    try:
        deadline = time.time() + 20
        while not (project / "calls.txt").exists() and time.time() < deadline:
            time.sleep(0.1)
        fake = pid_of(project, "run:t-1")
        time.sleep(0.5)
        os.killpg(running.pid, signal.SIGINT)  # what the terminal does on Ctrl-C
        running.wait(timeout=20)
    finally:
        if running.poll() is None:
            os.killpg(running.pid, signal.SIGKILL)
    assert running.returncode in (-signal.SIGINT, 130)
    deadline = time.time() + 5
    while is_running(fake) and time.time() < deadline:
        time.sleep(0.1)
    assert not is_running(fake)
    assert calls(project) == ["run:t-1"]  # the second run was never started


def test_where_a_command_was_waiting_is_written_down_before_it_is_stopped(project: Path) -> None:
    """The first uploader that stayed was ended by hand before anyone had looked. On a Mac the script now asks `sample` first."""
    tools = project / "bin"
    tools.mkdir()
    sample = tools / "sample"                     # stands in for the macOS tool: sample <pid> <seconds> -file <path>
    sample.write_text('#!/bin/sh\necho "Call graph of process $1" > "$4"\n', encoding="utf-8")
    sample.chmod(0o755)
    done = series(project, "t", "2", plan="upload:t-1=stay_after_results", PATH=f"{tools}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode == 0, done.stdout + done.stderr
    written = sorted((project / "logs").glob("stuck_*.txt"))
    assert len(written) == 1 and written[0].read_text(encoding="utf-8").startswith(f"Call graph of process {pid_of(project, 'upload:t-1')}")
    assert f"it was stopped. The run is on the dashboard. Where it was waiting: logs/{written[0].name}" in done.stdout
    assert "SERIES COMPLETE: 2 of 2 runs" in done.stdout
