"""Port69 v2 - Social Commands"""
from rich.table import Table
from cli.network.client import APIClient, APIError
from cli.ui.theme import (
    print_error, print_success, print_info,
    get_status_icon, format_timestamp, console
)


async def cmd_add(username: str):
    api = APIClient()
    try:
        with console.status(f"[green]Sending request to {username}...[/green]"):
            await api.post(f"/api/v1/friends/add/{username}")
        print_success(f"Friend request sent to [bold]{username}[/bold] 📨")
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_friends():
    api = APIClient()
    try:
        with console.status("[green]Loading friends...[/green]"):
            result = await api.get("/api/v1/friends/list")
        friends = result.get("friends", [])
        if not friends:
            print_info("No friends yet. Use [bold]port69 add <username>[/bold]")
            return

        table = Table(title="👥 Friends", border_style="bright_black", header_style="bold cyan")
        table.add_column("", width=3)
        table.add_column("Username", style="bright_cyan")
        table.add_column("Display Name")
        table.add_column("Status")
        table.add_column("Last Seen", style="dim")

        for f in friends:
            icon = get_status_icon(f.get("status", "offline"))
            sm = f.get("status_message", "") or ""
            last = format_timestamp(f.get("last_seen", "")) if f.get("last_seen") else "—"
            table.add_row(icon, f["username"], f.get("display_name", ""), sm, last)

        console.print(table)
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_requests():
    api = APIClient()
    try:
        with console.status("[green]Loading requests...[/green]"):
            result = await api.get("/api/v1/friends/requests")
        reqs = result.get("requests", [])
        if not reqs:
            print_info("No pending friend requests.")
            return

        table = Table(title="📨 Friend Requests", border_style="bright_black", header_style="bold cyan")
        table.add_column("Username", style="bright_cyan")
        table.add_column("Display Name")
        table.add_column("Sent", style="dim")
        table.add_column("Action", style="dim")

        for r in reqs:
            table.add_row(
                r["username"],
                r.get("display_name", ""),
                r.get("sent_at", "")[:10],
                f"port69 chat → /accept {r['username']}",
            )
        console.print(table)
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_users():
    api = APIClient()
    try:
        with console.status("[green]Fetching online users...[/green]"):
            result = await api.get("/api/v1/users/online")
        users = result.get("users", [])
        count = result.get("count", 0)

        if not users:
            print_info("No users online right now.")
            return

        table = Table(title=f"🟢 Online ({count})", border_style="green", header_style="bold green")
        table.add_column("", width=3)
        table.add_column("Username", style="bright_cyan")
        table.add_column("Display Name")
        table.add_column("Status Message", style="dim")

        for u in users:
            icon = get_status_icon(u.get("status", "online"))
            table.add_row(icon, u["username"], u.get("display_name", ""), u.get("status_message", "") or "")
        console.print(table)
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_whois(username: str):
    api = APIClient()
    try:
        with console.status(f"[green]Looking up {username}...[/green]"):
            u = await api.get(f"/api/v1/users/{username}")
        icon = get_status_icon(u.get("status", "offline"))
        console.print(f"\n[bold cyan]── {u['username']} ──[/bold cyan]")
        console.print(f"  Display:  {u.get('display_name', u['username'])}")
        console.print(f"  Status:   {icon} {u.get('status', 'offline')}")
        if u.get("status_message"):
            console.print(f"  Message:  {u['status_message']}")
        if u.get("bio"):
            console.print(f"  Bio:      {u['bio']}")
        console.print(f"  Messages: {u.get('total_messages', 0)}")
        console.print(f"  Joined:   {u.get('created_at', '')[:10]}\n")
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_stats():
    api = APIClient()
    try:
        with console.status("[green]Fetching stats...[/green]"):
            s = await api.get("/api/v1/stats")
        console.print("\n[bold cyan]── Port69 Server Stats ──[/bold cyan]")
        console.print(f"  👥 Total users:    [bold]{s.get('users', 0)}[/bold]")
        console.print(f"  🟢 Online now:     [bold bright_green]{s.get('online_now', 0)}[/bold bright_green]")
        console.print(f"  💬 Rooms:          [bold]{s.get('rooms', 0)}[/bold]")
        console.print(f"  📨 Messages sent:  [bold]{s.get('messages', 0)}[/bold]")
        console.print(f"  📎 Files shared:   [bold]{s.get('files', 0)}[/bold]\n")
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()


async def cmd_rooms():
    api = APIClient()
    try:
        with console.status("[green]Loading rooms...[/green]"):
            result = await api.get("/api/v1/rooms")
        rooms = result.get("rooms", [])
        if not rooms:
            print_info("No public rooms yet. Create one with: port69 chat → /create <name>")
            return

        table = Table(title="📢 Public Rooms", border_style="bright_black", header_style="bold cyan")
        table.add_column("Icon", width=4)
        table.add_column("Room", style="bright_cyan")
        table.add_column("Description")
        table.add_column("🟢 Online", justify="right")
        table.add_column("Messages", justify="right", style="dim")

        for r in rooms:
            table.add_row(
                r.get("icon", "💬"),
                f"#{r['name']}",
                r.get("description", "") or "",
                str(r.get("online", 0)),
                str(r.get("total_messages", 0)),
            )
        console.print(table)
        console.print("[dim]  Join with: port69 chat → /join <room>[/dim]\n")
    except APIError as e:
        print_error(f"Failed: {e.message}")
    finally:
        await api.close()
