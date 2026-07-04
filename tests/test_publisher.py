# tests/test_publisher.py
from pathlib import Path

from publisher import publish, should_publish


def test_should_publish_on_dirty_or_heartbeat():
    assert should_publish(True, False) is True
    assert should_publish(False, True) is True
    assert should_publish(True, True) is True
    assert should_publish(False, False) is False


def test_publish_writes_index_and_runs_git(tmp_path):
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        class R:  # noqa: E306
            returncode = 0
        return R()

    ok = publish("<html>hi</html>", worktree=tmp_path, runner=fake_runner)
    assert ok is True
    assert (tmp_path / "index.html").read_text() == "<html>hi</html>"
    joined = [" ".join(c) for c in calls]
    assert any("add" in c for c in joined)
    assert any("commit" in c for c in joined)
    assert any("push" in c and "--force" in c for c in joined)


def test_publish_never_raises_on_git_failure(tmp_path):
    def boom(cmd, **kwargs):
        raise RuntimeError("git exploded")

    ok = publish("<html>hi</html>", worktree=tmp_path, runner=boom)
    assert ok is False
