"""Tooling checks, kept separate from the application's feature test suite."""

import io
import json
import shutil
from types import SimpleNamespace

import pytest

import orchestrator
from tools import mcp_client, mcp_server
from tools.mcp_client import execute_mcp_tool


def test_stdio_reads_issue_and_app():
    """Use the real subprocess transport without API access or file edits."""
    assert "Add task priority" in execute_mcp_tool("fetch_github_issue", {
        "issue_number": 42,
    })
    paths = execute_mcp_tool("get_repo_structure").splitlines()
    assert "app.py" in paths
    assert "tools/llm.py" not in paths
    assert "Flask" in execute_mcp_tool("get_files", {"file_paths": ["app.py"]})
    assert "mocked locally" in execute_mcp_tool("publish_pull_request", {
        "issue_number": 42, "summary": "offline check",
    })


def test_stdio_applies_edits_in_disposable_checkout(tmp_path, monkeypatch):
    """Exercise a real MCP write without touching the working application."""
    (tmp_path / "tools").mkdir()
    shutil.copyfile(mcp_server.__file__, tmp_path / "tools/mcp_server.py")
    monkeypatch.setattr(mcp_client, "ROOT", tmp_path)
    execute_mcp_tool("apply_edits", {"edits": [
        {"file_path": "app.py", "content": "# disposable demo\n"},
    ]})
    assert (tmp_path / "app.py").read_text() == "# disposable demo\n"


@pytest.mark.parametrize("tool,arguments", [
    ("get_files", {"file_paths": ["tools/llm.py"]}),
    ("apply_edits", {"edits": [{"file_path": "../outside.py", "content": "x"}]}),
    ("missing_tool", {}),
])
def test_stdio_reports_tool_errors(tool, arguments):
    """Server-side failures must raise rather than look like success."""
    with pytest.raises(RuntimeError, match="MCP tool"):
        execute_mcp_tool(tool, arguments)


def test_edits_are_validated_before_writing(tmp_path, monkeypatch):
    """Reject the complete edit batch before changing an allowed file."""
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    target = tmp_path / "app.py"
    target.write_text("original")
    with pytest.raises(ValueError):
        mcp_server.apply_edits([
            {"file_path": "app.py", "content": "replacement"},
            {"file_path": "tools/llm.py", "content": "not allowed"},
        ])
    assert target.read_text() == "original"


def test_issue_fetch_uses_enterprise_api(monkeypatch):
    """Use the runner's API host instead of hardcoding GitHub.com."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "course/demo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-placeholder")
    monkeypatch.setenv("GITHUB_API_URL", "https://github.gatech.edu/api/v3")

    def fake_urlopen(request):
        assert request.full_url == (
            "https://github.gatech.edu/api/v3/repos/course/demo/issues/42")
        return io.BytesIO(b'{"title":"Priority","body":"demo"}')

    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", fake_urlopen)
    assert "Priority" in mcp_server.fetch_github_issue(42)


def test_pytest_uses_current_python_without_tokens(monkeypatch):
    """QA uses the current interpreter and omits credentials from its env."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-placeholder")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-placeholder")

    def fake_run(command, **kwargs):
        assert command[:3] == [mcp_server.sys.executable, "-m", "pytest"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "GITHUB_TOKEN" not in kwargs["env"]
        assert kwargs["cwd"] == mcp_server.ROOT
        return SimpleNamespace(returncode=1, stdout="one failure", stderr="")

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
    result = mcp_server.run_test_suite()
    assert result["passed"] is False
    assert "one failure" in result["output"]


@pytest.mark.parametrize("run_id,attempt", [("101", "1"), ("101", "2"), ("102", "1")])
def test_publish_keeps_git_output_off_mcp_stdout(monkeypatch, run_id, attempt):
    """Mock publication: no git push or GitHub request leaves this test."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "course/demo")
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-placeholder")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    branch = f"feature-issue-42-{run_id}-{attempt}"

    def fake_run(command, **kwargs):
        assert kwargs["capture_output"] is True
        if command[:3] == ["git", "checkout", "-b"]:
            assert command[3] == branch
        if command[:2] == ["git", "push"]:
            assert command[-1] == branch
        return SimpleNamespace(stdout="M app.py", stderr="", returncode=0)

    def fake_urlopen(request):
        assert json.loads(request.data)["draft"] is True
        assert json.loads(request.data)["head"] == branch
        return io.BytesIO(b'{"html_url":"https://example.test/pull/1"}')

    monkeypatch.setattr(mcp_server.subprocess, "run", fake_run)
    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", fake_urlopen)
    assert "Successfully created PR" in mcp_server.publish_pull_request(42, "demo")


@pytest.mark.parametrize("passes,decisions,published", [
    ([True, True], ["REQUEST_MORE_TESTS", "APPROVE"], True),
    ([False], ["APPROVE"], False),
    ([True], ["REQUEST_CHANGES"], False),
])
def test_multi_requires_tests_and_review(monkeypatch, passes, decisions, published):
    """Keep the upstream feedback loop and both publication gates intact."""
    contexts, publications = [], []
    results = iter(passes)
    reviews = iter(decisions)

    def tool(name, arguments=None):
        if name == "get_files":
            contexts.append(list(arguments["file_paths"]))
        if name == "run_test_suite":
            return {"passed": next(results), "output": "actual pytest output"}
        if name == "publish_pull_request":
            publications.append(arguments)
        return "tool output"

    monkeypatch.setattr(orchestrator, "execute_mcp_tool", tool)
    monkeypatch.setattr(orchestrator, "create_plan",
                        lambda *args: {"plan": "Add priority",
                                       "files_to_modify": ["app.py"]})
    monkeypatch.setattr(orchestrator, "implement_feature",
                        lambda *args: {"edits": [], "summary": "demo"})
    monkeypatch.setattr(orchestrator, "review_feature",
                        lambda *args: {"status": next(reviews),
                                       "feedback": "add feature tests"})
    if published:
        orchestrator.run_multi_agent_pipeline(42, max_attempts=len(passes))
    else:
        with pytest.raises(RuntimeError, match="Pipeline failed"):
            orchestrator.run_multi_agent_pipeline(42, max_attempts=len(passes))
    assert bool(publications) is published
    if len(passes) > 1:
        assert contexts == [["app.py"], ["app.py", "tests/test_app.py"]]
