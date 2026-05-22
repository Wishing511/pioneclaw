"""
PioneClaw CLI 入口

Usage:
    pioneclaw chat                  # 交互式聊天
    pioneclaw chat -m "你好"        # 单次消息
    pioneclaw task list             # 任务列表
    pioneclaw task create "标题"    # 创建任务
    pioneclaw task complete 1       # 完成任务
    pioneclaw skill list            # 技能列表
    pioneclaw skill reload          # 热重载技能
    pioneclaw run                   # 启动 Web 服务
    pioneclaw version               # 版本信息
"""

import logging
import sys
from pathlib import Path

import typer

# 在任何其他导入之前抑制所有第三方库的 INFO 日志
# 这必须在导入 sqlalchemy 之前设置
logging.getLogger("sqlalchemy").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.pool").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.dialects").setLevel(logging.ERROR)
logging.getLogger("sqlalchemy.orm").setLevel(logging.ERROR)
# 同时禁用传播
for name in [
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "sqlalchemy.dialects",
    "sqlalchemy.orm",
]:
    logger = logging.getLogger(name)
    logger.propagate = False

# 在导入 app 之前，确保工作目录正确
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 加载 .env
try:
    from dotenv import load_dotenv

    _env_file = _BACKEND_DIR / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass


app = typer.Typer(
    name="pioneclaw",
    help="PioneClaw - 企业级智能协作平台 CLI",
    add_completion=False,
    no_args_is_help=True,
)

chat_app = typer.Typer(help="对话管理", no_args_is_help=True)
task_app = typer.Typer(help="任务管理", no_args_is_help=True)
skill_app = typer.Typer(help="技能管理", no_args_is_help=True)

app.add_typer(chat_app, name="chat")
app.add_typer(task_app, name="task")
app.add_typer(skill_app, name="skill")


# ==================== version ====================


@app.command()
def version():
    """显示版本信息"""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    from app.core.config import settings

    console.print(
        Panel(
            f"[bold cyan]PioneClaw[/bold cyan] v{settings.VERSION}\n"
            f"[dim]{settings.APP_NAME} - 企业级智能协作平台[/dim]",
            title="Version",
            border_style="cyan",
        )
    )


# ==================== run ====================


@app.command()
def run(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="监听地址"),
    port: int = typer.Option(8000, "--port", "-p", help="监听端口"),
    reload: bool = typer.Option(False, "--reload", help="热重载模式"),
    workers: int = typer.Option(1, "--workers", "-w", help="工作进程数"),
):
    """启动 Web 服务"""
    import uvicorn
    from rich.console import Console

    console = Console()
    console.print("[bold cyan]Starting PioneClaw server...[/bold cyan]")
    console.print(f"  Host: [green]{host}[/green]")
    console.print(f"  Port: [green]{port}[/green]")
    console.print(f"  Reload: [yellow]{reload}[/yellow]")
    console.print(f"  Docs: [link]http://localhost:{port}/docs[/link]")
    console.print()

    import asyncio

    try:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            reload=reload,
            workers=workers if not reload else 1,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[dim]Server shutting down...[/dim]")
    except Exception as e:
        console.print(f"\n[red]Server error: {e}[/red]")


# ==================== chat ====================


@chat_app.command("start")
def chat_start(
    message: str | None = typer.Option(
        None, "--message", "-m", help="单次消息（非交互模式）"
    ),
    model_config_id: int | None = typer.Option(None, "--model", help="模型配置 ID"),
    temperature: float | None = typer.Option(None, "--temperature", "-t", help="温度"),
    max_tokens: int | None = typer.Option(None, "--max-tokens", help="最大 token"),
):
    """交互式对话"""
    import asyncio

    asyncio.run(_chat_interactive(message, model_config_id, temperature, max_tokens))


async def _chat_interactive(
    single_message: str | None = None,
    model_config_id: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """交互式聊天实现"""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    console.print(
        Panel(
            "[bold cyan]PioneClaw Chat[/bold cyan]\n"
            "[dim]输入消息开始对话，输入 /quit 退出，/clear 清空历史[/dim]",
            border_style="cyan",
        )
    )

    # 初始化数据库和依赖
    from app.core.database import init_db

    await init_db()

    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import AIModelConfig, User

    # 获取默认模型配置
    async with async_session_maker() as db:
        if model_config_id:
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.id == model_config_id)
            )
        else:
            result = await db.execute(
                select(AIModelConfig).where(
                    AIModelConfig.is_default, AIModelConfig.is_active
                )
            )
        config = result.scalar_one_or_none()

        if not config:
            # 尝试任意激活配置
            result = await db.execute(
                select(AIModelConfig).where(AIModelConfig.is_active).limit(1)
            )
            config = result.scalar_one_or_none()

        if not config:
            console.print("[red]没有可用的 AI 模型配置，请先在 Web UI 中添加配置[/red]")
            return

        # 获取 admin 用户（CLI 默认使用 admin）
        result = await db.execute(select(User).where(User.is_super_admin).limit(1))
        result.scalar_one_or_none()

    console.print(
        f"[dim]模型: {config.display_name or config.model_name} | 提供商: {config.provider}[/dim]"
    )
    console.print()

    # 构建聊天客户端
    from app.api.chat import SimpleLLMProvider

    provider = SimpleLLMProvider(config=config)

    if temperature is not None:
        provider.temperature = temperature
    if max_tokens is not None:
        provider.max_tokens = max_tokens

    # 对话历史
    history: list[dict] = []

    # 如果有单次消息
    if single_message:
        await _send_message(provider, history, single_message, console)
        return

    # 交互循环
    while True:
        try:
            user_input = console.input("[bold green]You>[/bold green] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input == "/quit" or user_input == "/exit":
            console.print("[dim]Bye![/dim]")
            break
        elif user_input == "/clear":
            history.clear()
            console.print("[dim]对话已清空[/dim]")
            continue
        elif user_input == "/help":
            console.print("[dim]/quit - 退出  /clear - 清空历史  /help - 帮助[/dim]")
            continue

        await _send_message(provider, history, user_input, console)


async def _send_message(provider, history: list, user_input: str, console):
    """发送消息并显示回复"""
    import time

    from rich.markdown import Markdown
    from rich.panel import Panel

    history.append({"role": "user", "content": user_input})

    console.print()
    start_time = time.time()
    full_response = ""

    try:
        async for chunk in provider.chat_stream(messages=list(history)):
            if "error" in chunk:
                console.print(f"[red]Error: {chunk['error']}[/red]")
                break
            if "content" in chunk:
                full_response += chunk["content"]

        elapsed = time.time() - start_time

        if full_response.strip():
            history.append({"role": "assistant", "content": full_response})
            console.print(
                Panel(
                    Markdown(full_response),
                    title=f"Assistant ({elapsed:.1f}s)",
                    border_style="blue",
                )
            )

            # Token 信息
            if provider.last_input_tokens or provider.last_output_tokens:
                console.print(
                    f"[dim]Tokens: {provider.last_input_tokens} in / "
                    f"{provider.last_output_tokens} out[/dim]"
                )
    except Exception as e:
        console.print(f"[red]请求失败: {e}[/red]")
        # 移除失败的 user 消息
        if history and history[-1]["role"] == "user":
            history.pop()

    console.print()


# ==================== task ====================


@task_app.command("list")
def task_list(
    status: str | None = typer.Option(None, "--status", "-s", help="按状态筛选"),
    limit: int = typer.Option(20, "--limit", "-n", help="显示数量"),
):
    """列出任务"""
    import asyncio

    asyncio.run(_task_list(status, limit))


async def _task_list(status_filter: str | None, limit: int):
    from rich.console import Console
    from rich.table import Table

    console = Console()
    from app.core.database import init_db

    await init_db()

    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import Task

    async with async_session_maker() as db:
        query = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status_filter:
            query = query.where(Task.status == status_filter)
        result = await db.execute(query)
        tasks = result.scalars().all()

    if not tasks:
        console.print("[dim]没有任务[/dim]")
        return

    table = Table(title="Tasks", border_style="cyan")
    table.add_column("ID", style="cyan", width=6)
    table.add_column("Title", style="white")
    table.add_column("Status", style="green")
    table.add_column("Priority", style="yellow", width=8)
    table.add_column("Type", style="blue", width=8)
    table.add_column("Created", style="dim")

    STATUS_COLORS = {
        "pending": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "cancelled": "dim",
    }

    for t in tasks:
        status_style = STATUS_COLORS.get(t.status, "white")
        table.add_row(
            str(t.id),
            t.title[:50],
            f"[{status_style}]{t.status}[/{status_style}]",
            t.priority or "normal",
            t.task_type or "manual",
            t.created_at.strftime("%m-%d %H:%M") if t.created_at else "-",
        )

    console.print(table)


@task_app.command("create")
def task_create(
    title: str = typer.Argument(..., help="任务标题"),
    description: str = typer.Option("", "--desc", "-d", help="任务描述"),
    priority: str = typer.Option("normal", "--priority", "-p", help="优先级"),
    task_type: str = typer.Option("manual", "--type", "-t", help="任务类型"),
):
    """创建任务"""
    import asyncio

    asyncio.run(_task_create(title, description, priority, task_type))


async def _task_create(title: str, description: str, priority: str, task_type: str):
    from rich.console import Console

    console = Console()

    from app.core.database import init_db

    await init_db()

    from app.core.database import async_session_maker
    from app.models import Task

    async with async_session_maker() as db:
        task = Task(
            title=title,
            description=description,
            priority=priority,
            task_type=task_type,
            status="pending",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        console.print(f"[green]Task #{task.id} created:[/green] {title}")


@task_app.command("complete")
def task_complete(
    task_id: int = typer.Argument(..., help="任务 ID"),
):
    """完成任务"""
    import asyncio

    asyncio.run(_task_complete(task_id))


async def _task_complete(task_id: int):
    from datetime import datetime, timezone

    from rich.console import Console

    console = Console()

    from app.core.database import init_db

    await init_db()

    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import Task

    async with async_session_maker() as db:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            console.print(f"[red]Task #{task_id} not found[/red]")
            return
        task.status = "completed"
        task.completed_at = datetime.now(tz=timezone.utc)
        await db.commit()
        console.print(f"[green]Task #{task_id} completed[/green]")


# ==================== skill ====================


@skill_app.command("list")
def skill_list():
    """列出技能"""
    import asyncio

    asyncio.run(_skill_list())


async def _skill_list():
    from rich.console import Console
    from rich.table import Table

    console = Console()
    from app.core.database import init_db

    await init_db()

    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import Skill

    async with async_session_maker() as db:
        result = await db.execute(select(Skill).order_by(Skill.created_at.desc()))
        skills = result.scalars().all()

    if not skills:
        console.print("[dim]没有技能[/dim]")
        return

    table = Table(title="Skills", border_style="cyan")
    table.add_column("ID", style="cyan", width=6)
    table.add_column("Name", style="white")
    table.add_column("Display", style="white")
    table.add_column("Category", style="blue", width=10)
    table.add_column("Active", width=6)
    table.add_column("Always", width=6)
    table.add_column("Format", style="dim", width=8)

    for s in skills:
        table.add_row(
            str(s.id),
            s.name,
            s.display_name,
            s.category,
            "[green]Y[/green]" if s.is_active else "[red]N[/red]",
            "[yellow]Y[/yellow]" if s.always_activate else "",
            s.skill_format,
        )

    console.print(table)


@skill_app.command("reload")
def skill_reload():
    """热重载技能"""
    import asyncio

    asyncio.run(_skill_reload())


async def _skill_reload():
    from rich.console import Console

    console = Console()

    from app.core.database import init_db

    await init_db()

    from app.modules.agent.skills import get_skills_loader

    loader = get_skills_loader()
    if loader:
        await loader.reload()
        count = len(loader.list_skills())
        console.print(f"[green]Skills reloaded: {count} skills loaded[/green]")
    else:
        console.print("[yellow]SkillsLoader not initialized[/yellow]")


# ==================== entry point ====================


def main():
    """CLI 入口"""
    app()


if __name__ == "__main__":
    main()
