"""Port69 - UI Theme & Helpers"""
import re
from datetime import datetime
from rich.console import Console

console = Console()

COLORS = {
    "primary":   "bright_green",
    "secondary": "cyan",
    "accent":    "bright_yellow",
    "error":     "bold red",
    "warning":   "yellow",
    "success":   "bold bright_green",
    "muted":     "dim white",
    "system":    "dim italic cyan",
    "dm":        "bright_magenta",
    "self":      "bright_white",
}

STATUS_ICONS = {
    "online":    "[bold green]●[/bold green]",
    "away":      "[bold yellow]●[/bold yellow]",
    "busy":      "[bold red]●[/bold red]",
    "invisible": "[dim]●[/dim]",
    "offline":   "[dim]○[/dim]",
}

USER_COLORS = [
    "bright_cyan", "bright_magenta", "bright_yellow",
    "bright_blue", "bright_red", "cyan", "magenta",
    "yellow", "green", "bright_green", "blue",
]

BANNER = r"""
  ____            __  ___  ____
 / __ \____  ____/ /_/ _ \/ __ \
/ /_/ / __ \/ __/ __/  __/ /_/ /
\____/\____/\__/\__/\___/\____/
"""


def print_banner():
    console.print(f"[bold bright_green]{BANNER}[/bold bright_green]")
    console.print("[dim]  Terminal chat. Talk to friends. Share files.[/dim]\n")


def print_mini_banner():
    console.print("⚡ [bold bright_green]Port69[/bold bright_green] [dim]— Terminal Communication[/dim]")


def print_error(msg: str):
    console.print(f"[bold red]✗[/bold red] {msg}")


def print_success(msg: str):
    console.print(f"[bold bright_green]✓[/bold bright_green] {msg}")


def print_info(msg: str):
    console.print(f"[dim cyan]ℹ[/dim cyan] {msg}")


def print_warning(msg: str):
    console.print(f"[yellow]⚠[/yellow] {msg}")


def get_user_color(username: str) -> str:
    return USER_COLORS[sum(ord(c) for c in username) % len(USER_COLORS)]


def get_status_icon(status: str) -> str:
    return STATUS_ICONS.get(status, STATUS_ICONS["offline"])


def format_timestamp(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.utcnow()
        diff = (now - dt.replace(tzinfo=None)).total_seconds()
        if diff < 60:
            return "just now"
        elif diff < 3600:
            return f"{int(diff/60)}m ago"
        elif dt.date() == now.date():
            return dt.strftime("%H:%M")
        else:
            return dt.strftime("%b %d %H:%M")
    except Exception:
        return ts[:16] if ts else ""


def format_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size //= 1024
    return f"{size:.1f}GB"


def render_content(text: str) -> str:
    text = re.sub(r'```(\w+)?\n?(.*?)```', lambda m: f"[bold bright_yellow on black] {m.group(2).strip()} [/bold bright_yellow on black]", text, flags=re.DOTALL)
    text = re.sub(r'`([^`]+)`', r'[bold bright_yellow on black] \1 [/bold bright_yellow on black]', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'[bold]\1[/bold]', text)
    text = re.sub(r'\*(.+?)\*', r'[italic]\1[/italic]', text)
    text = re.sub(r'~~(.+?)~~', r'[strike]\1[/strike]', text)
    text = re.sub(r'(https?://\S+)', r'[underline cyan]\1[/underline cyan]', text)
    text = re.sub(r'@(\w+)', r'[bold bright_yellow]@\1[/bold bright_yellow]', text)
    text = re.sub(r'#(\w+)', r'[bold cyan]#\1[/bold cyan]', text)
    return text


def make_avatar(username: str, color: str = None) -> str:
    letter = username[0].upper() if username else "?"
    c = color or get_user_color(username)
    return f"[bold {c}]{letter}[/bold {c}]"


def progress_bar(current: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "[" + "─" * width + "]"
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(100 * current / total)
    return f"[green]{bar}[/green] {pct}%"
