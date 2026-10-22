import os
from pathlib import Path
import sys
import subprocess
import json
import urllib.request
from urllib.error import HTTPError
import textwrap
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("WorkshopTools")
ROOT = Path(__file__).resolve().parents[1]
APP_FILES = ("app.py", "static/app.js", "static/style.css",
             "templates/index.html", "tests/test_app.py")


def _allowed_path(name: str) -> Path:
    """Keep agent reads and edits inside the small exercise."""
    path = (ROOT / name).resolve()
    if path.relative_to(ROOT).as_posix() not in APP_FILES:
        raise ValueError(f"File is outside the exercise: {name}")
    return path


@mcp.tool()
def read_coding_guidelines() -> str:
    try:
        with open("docs/coding_guidelines.md", "r") as f:
            return f.read()
    except FileNotFoundError:
        return "Warning: Coding guidelines document not found."

@mcp.tool()
def get_repo_structure() -> str:
    return "\n".join(name for name in APP_FILES if (ROOT / name).exists())

@mcp.tool()
def get_files(file_paths: list[str]) -> str:
    contents = []
    for path in file_paths:
        checked = _allowed_path(path)
        if checked.exists():
            with open(checked, 'r') as f:
                contents.append(f"--- FILE: {path} ---\n{f.read()}\n")
        else:
            contents.append(f"--- FILE: {path} ---\n(File does not exist)\n")
    return "\n".join(contents)

@mcp.tool()
def apply_edits(edits: list[dict]) -> str:
    checked = [(_allowed_path(edit["file_path"]), edit["content"])
               for edit in edits]
    if any(not isinstance(content, str) for _, content in checked):
        raise ValueError("Each edit needs string content")
    for file_path, content in checked:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
    return f"Successfully applied {len(edits)} edits."

@mcp.tool()
def run_test_suite() -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v"],
            capture_output=True, text=True, timeout=30, cwd=ROOT,
            env={**{key: os.environ[key] for key in
                    ("PATH", "TMPDIR", "SYSTEMROOT", "WINDIR")
                    if key in os.environ}, "PYTHONPATH": str(ROOT)},
        )
        return {"passed": result.returncode == 0, "output": result.stdout + "\n" + result.stderr}
    except Exception as e:
        return {"passed": False, "output": f"Test execution failed: {str(e)}"}

@mcp.tool()
def get_repository_diff() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", *APP_FILES],
            capture_output=True, text=True, cwd=ROOT, check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Could not retrieve git diff") from error

@mcp.tool()
def fetch_github_issue(issue_number: int) -> str:
    if not os.getenv("GITHUB_ACTIONS"):
        return textwrap.dedent("""\
            Title: Add task priority
            Body:
            - Tasks can be Low, Medium, or High priority.
            - The user can choose priority when creating a task.
            - The backend stores and returns the priority.
            - The frontend displays the priority.
            - Missing priority defaults to Medium.
            - Existing behavior should continue working.
        """)

    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token or not issue_number:
        raise RuntimeError("Missing GitHub Action environment variables")

    api_base = os.getenv("GITHUB_API_URL", "https://api.github.com")
    url = f"{api_base}/repos/{repo}/issues/{issue_number}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "TA-Workshop-Demo-Agent"
    })

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return f"Title: {data.get('title')}\nBody: {data.get('body')}"
    except HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"GitHub API Error {e.code}: {error_body}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to fetch issue: {e}") from e

@mcp.tool()
def publish_pull_request(issue_number: int, summary: str) -> str:
    if not os.getenv("GITHUB_ACTIONS"):
        return "PR creation mocked locally."

    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    # Separate branches for repeated demos and Actions re-runs of one issue.
    run_id = os.getenv("GITHUB_RUN_ID", "manual")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    branch_name = f"feature-issue-{issue_number}-{run_id}-{attempt}"

    try:
        # Git output must not mix with the MCP messages on stdout.
        git_options = {"check": True, "capture_output": True, "text": True}
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], **git_options)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], **git_options)
        subprocess.run(["git", "checkout", "-b", branch_name], **git_options)
        subprocess.run(["git", "add", "--", *APP_FILES], **git_options)

        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return "No file changes detected; aborted pull request creation."

        subprocess.run(["git", "commit", "-m", f"Implement feature for issue #{issue_number}"], **git_options)
        subprocess.run(["git", "push", "-u", "origin", branch_name], **git_options)

        api_base = os.getenv("GITHUB_API_URL", "https://api.github.com")
        url = f"{api_base}/repos/{repo}/pulls"
        data = json.dumps({
            "title": f"Automated Feature Implementation (Issue #{issue_number})",
            "body": f"Resolves #{issue_number}.\n\n### Summary of Changes:\n{summary}",
            "head": branch_name,
            "base": "main",
            "draft": True
        }).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "TA-Workshop-Demo-Agent"
        }, method="POST")

        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
            return f"Successfully created PR: {res_data.get('html_url')}"

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git operation failed: {e}") from e
    except HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"GitHub API Error {e.code}: {error_body}") from e
    except Exception as e:
        raise RuntimeError(f"Failed to create PR: {e}") from e

if __name__ == "__main__":
    # stdout carries MCP messages, not print-based diagnostics.
    mcp.run(transport="stdio")
