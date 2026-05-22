"""
Git 上下文注入 — 检测仓库状态并生成 Agent 提示词摘要
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GitContext:
    branch: str = ""
    last_commit: str = ""
    last_commit_msg: str = ""
    unstaged: list[str] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    has_repo: bool = False


def get_git_context(working_dir: str | None = None) -> GitContext:
    """获取当前目录 Git 上下文"""
    cwd = Path(working_dir) if working_dir else Path.cwd()

    # Find .git directory (walk up)
    git_dir = None
    p = cwd
    for _ in range(10):
        if (p / ".git").exists():
            git_dir = p / ".git"
            cwd = p
            break
        if p.parent == p:
            break
        p = p.parent

    if not git_dir:
        return GitContext()

    ctx = GitContext(has_repo=True)

    def _run(args: list) -> str:
        try:
            r = subprocess.run(
                ["git"] + args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    # Branch
    ctx.branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])

    # Last commit
    log = _run(["log", "-1", "--format=%h %s"])
    if log:
        parts = log.split(" ", 1)
        ctx.last_commit = parts[0]
        ctx.last_commit_msg = parts[1] if len(parts) > 1 else ""

    # Unstaged changes
    unstaged = _run(["diff", "--name-only"])
    if unstaged:
        ctx.unstaged = [f for f in unstaged.split("\n") if f]

    # Staged changes
    staged = _run(["diff", "--cached", "--name-only"])
    if staged:
        ctx.staged = [f for f in staged.split("\n") if f]

    # Recent commits (last 5)
    recent = _run(["log", "-5", "--format=%h %s"])
    if recent:
        ctx.recent_commits = [line for line in recent.split("\n") if line]

    return ctx


def format_git_prompt(ctx: GitContext) -> str:
    """生成注入系统提示词的 Git 上下文文本"""
    if not ctx.has_repo:
        return ""

    lines = ["<git_context>"]
    lines.append(f"Branch: {ctx.branch}")
    if ctx.last_commit:
        lines.append(f"Last commit: {ctx.last_commit} {ctx.last_commit_msg}")

    if ctx.staged:
        lines.append(f"Staged: {', '.join(ctx.staged[:10])}")
    if ctx.unstaged:
        lines.append(f"Unstaged: {', '.join(ctx.unstaged[:10])}")

    if ctx.recent_commits:
        lines.append("Recent commits:")
        for c in ctx.recent_commits[:5]:
            lines.append(f"  {c}")

    lines.append("</git_context>")
    return "\n".join(lines)
