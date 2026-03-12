"""Port69 v2 - Full Terminal Chat UI"""
import asyncio
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Deque
from collections import deque

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.live import Live
from rich.columns import Columns
from rich.rule import Rule
from rich.markup import escape

from cli.config import config
from cli.network.client import APIClient, WSClient, APIError
from cli.ui.theme import (
    get_user_color, get_status_icon, format_timestamp,
    render_content, make_avatar, format_size, progress_bar,
    print_error, print_success, print_info, COLORS
)

console = Console()


class ChatMessage:
    def __init__(self, sender: str, content: str, timestamp: str,
                 msg_id: int = 0, is_self: bool = False,
                 is_system: bool = False, msg_type: str = "text",
                 reply_to: dict = None, reactions: dict = None,
                 avatar_color: str = None, display_name: str = None,
                 is_edited: bool = False, file_info: dict = None,
                 poll_data: dict = None):
        self.sender = sender
        self.display_name = display_name or sender
        self.content = content
        self.timestamp = timestamp
        self.msg_id = msg_id
        self.is_self = is_self
        self.is_system = is_system
        self.msg_type = msg_type
        self.reply_to = reply_to
        self.reactions = reactions or {}
        self.avatar_color = avatar_color
        self.is_edited = is_edited
        self.file_info = file_info
        self.poll_data = poll_data


class ChatUI:
    MAX_MSGS = 300
    INPUT_HISTORY_SIZE = 100

    def __init__(self, username: str, target: Optional[str] = None):
        self.username = username
        self.target = target
        self.current_room = "general"
        self.messages: Deque[ChatMessage] = deque(maxlen=self.MAX_MSGS)
        self.notifications: Deque[dict] = deque(maxlen=15)
        self.online_users: List[dict] = []
        self.friends: List[dict] = []
        self.my_rooms: List[dict] = []
        self.typing_users: set = set()
        self.ws: Optional[WSClient] = None
        self.api: Optional[APIClient] = None
        self._running = False
        self._reply_to: Optional[int] = None  # reply-to message id
        self._reply_preview: Optional[str] = None
        self._last_sender: Optional[str] = None
        self._unread: Dict[str, int] = {}  # room → unread count
        self._polls: Dict[int, dict] = {}  # poll_id → poll data
        self._input_hist: List[str] = list(config.input_history)
        self._hist_idx: int = -1
        self._msg_count: int = 0

    async def run(self):
        self.api = APIClient()
        self._running = True

        # Determine room
        if self.target:
            self.current_room = "dm_" + "_".join(sorted([self.username, self.target]))
        else:
            self.current_room = "general"
            try:
                await self.api.post("/api/v1/rooms", {"name": "general", "description": "General chat", "icon": "👋"})
            except APIError:
                pass
            try:
                await self.api.post("/api/v1/rooms/general/join")
            except APIError:
                pass

        # Load initial data
        await self._load_history()
        await self._refresh_sidebar()

        # Connect WS
        self.ws = WSClient(on_message=self._on_ws)
        try:
            await self.ws.connect()
        except Exception as e:
            print_error(f"Cannot connect to server: {e}")
            return

        await self.ws.join_room(self.current_room)

        try:
            await self._loop()
        finally:
            await self.ws.disconnect()
            await self.api.close()

    async def _loop(self):
        ws_task = asyncio.create_task(self.ws.listen())
        refresh_task = asyncio.create_task(self._periodic_refresh())
        typing_task = asyncio.create_task(self._typing_cleanup())

        self._render()

        try:
            while self._running:
                line = await asyncio.get_event_loop().run_in_executor(None, self._read_input)
                if line is None:
                    break
                await self._handle_input(line.strip())
                self._render()
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            for t in [ws_task, refresh_task, typing_task]:
                t.cancel()
            console.print("\n[dim]Goodbye! 👋[/dim]")

    def _read_input(self) -> Optional[str]:
        try:
            room_display = self._room_label()
            typing_txt = ""
            if self.typing_users:
                names = ", ".join(list(self.typing_users)[:3])
                typing_txt = f" [dim italic]{names} typing...[/dim italic]"
            reply_txt = ""
            if self._reply_to:
                reply_txt = f" [dim]↩ replying to msg#{self._reply_to}[/dim]"

            console.print(f"\n{room_display}{typing_txt}{reply_txt} [bright_green]❯[/bright_green] ", end="")
            return input()
        except (EOFError, KeyboardInterrupt):
            return None

    async def _handle_input(self, text: str):
        if not text:
            return

        # Save to input history
        config.add_history(text)

        if text.lower() in ("/quit", "/exit", "/q"):
            self._running = False
            return

        if text.startswith("/"):
            await self._handle_command(text)
        else:
            # Send message
            await self.ws.send_message(
                self.current_room, text,
                reply_to=self._reply_to,
            )
            self._reply_to = None
            self._reply_preview = None
            # Optimistic display
            self.messages.append(ChatMessage(
                sender=self.username,
                content=text,
                timestamp=format_timestamp(datetime.utcnow().isoformat()),
                is_self=True,
                avatar_color=config.avatar_color,
            ))
            await self.ws.typing_stop(self.current_room)

    async def _handle_command(self, cmd: str):
        parts = cmd.strip().split(maxsplit=3)
        c = parts[0].lower()

        # Navigation
        if c == "/join":
            await self._cmd_join(parts)
        elif c == "/leave":
            await self._cmd_leave()
        elif c in ("/create", "/create-room"):
            await self._cmd_create(parts)
        elif c == "/rooms":
            await self._cmd_rooms()
        elif c == "/msg":
            await self._cmd_dm(parts)

        # Messages
        elif c == "/history":
            await self._cmd_history(parts)
        elif c == "/reply":
            await self._cmd_reply(parts)
        elif c == "/edit":
            await self._cmd_edit(parts)
        elif c == "/delete":
            await self._cmd_delete(parts)
        elif c == "/react":
            await self._cmd_react(parts)
        elif c == "/pin":
            await self._cmd_pin(parts)

        # Files
        elif c in ("/sendfile", "/file", "/sf"):
            await self._cmd_sendfile(parts)
        elif c in ("/download", "/dl"):
            await self._cmd_download(parts)

        # Friends & Users
        elif c == "/add":
            await self._cmd_add(parts)
        elif c == "/accept":
            await self._cmd_accept(parts)
        elif c == "/reject":
            await self._cmd_reject(parts)
        elif c == "/friends":
            await self._cmd_friends()
        elif c == "/requests":
            await self._cmd_requests()
        elif c == "/users":
            await self._cmd_users()
        elif c == "/whois":
            await self._cmd_whois(parts)

        # Polls
        elif c == "/poll":
            await self._cmd_poll(parts)
        elif c == "/vote":
            await self._cmd_vote(parts)

        # Status
        elif c == "/status":
            await self._cmd_status(parts)
        elif c == "/away":
            await self._set_status("away", "Away from keyboard")
        elif c == "/busy":
            await self._set_status("busy", "Do not disturb")
        elif c == "/back":
            await self._set_status("online", "")

        # Utility
        elif c == "/stats":
            await self._cmd_stats()
        elif c == "/search":
            await self._cmd_search(parts)
        elif c == "/clear":
            self.messages.clear()
        elif c == "/me":
            await self._cmd_me(parts)
        elif c in ("/help", "/?"):
            self._show_help()
        else:
            self._sys(f"[red]Unknown command:[/red] {c}  —  type [bold]/help[/bold]")

    # ── COMMANDS ───────────────────────────────────────────────────

    async def _cmd_join(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /join <room>"); return
        name = parts[1].lower()
        try:
            await self.api.post(f"/api/v1/rooms/{name}/join")
            await self.ws.join_room(name)
            self.current_room = name
            await self._load_history()
            self._sys(f"[green]✓ Joined [bold]#{name}[/bold][/green]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_leave(self):
        try:
            await self.api.post(f"/api/v1/rooms/{self.current_room}/leave")
            await self.ws.leave_room(self.current_room)
            self.current_room = "general"
            await self._load_history()
            self._sys("[yellow]Left room — back in #general[/yellow]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_create(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /create <room-name> [description]"); return
        name = parts[1].lower()
        desc = parts[2] if len(parts) > 2 else ""
        try:
            await self.api.post("/api/v1/rooms", {"name": name, "description": desc})
            await self.ws.join_room(name)
            self.current_room = name
            self._sys(f"[green]✓ Created and joined [bold]#{name}[/bold][/green]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_rooms(self):
        try:
            result = await self.api.get("/api/v1/rooms")
            rooms = result.get("rooms", [])
            self._sys(f"[cyan]📢 Public Rooms ({len(rooms)}):[/cyan]")
            for r in rooms:
                online = r.get("online", 0)
                total = r.get("total_messages", 0)
                icon = r.get("icon", "💬")
                self._sys(f"  {icon} [bold]#{r['name']}[/bold] — {r.get('description','')} [{online} online, {total} msgs]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_dm(self, parts):
        if len(parts) < 3:
            self._sys("Usage: /msg <username> <message>"); return
        target, content = parts[1], parts[2]
        dm_room = "dm_" + "_".join(sorted([self.username, target]))
        try:
            await self.api.post(f"/api/v1/friends/accept/{target}")
        except APIError:
            pass
        await self.ws.join_room(dm_room)
        await self.ws.send_message(dm_room, content)
        self.current_room = dm_room
        self._sys(f"[magenta]→ DM sent to {target}[/magenta]")

    async def _cmd_history(self, parts):
        room = parts[1].lower() if len(parts) > 1 else self.current_room
        try:
            result = await self.api.get(f"/api/v1/messages/history/{room}?limit=30")
            msgs = result.get("messages", [])
            self._sys(f"[cyan]── History: #{room} ──[/cyan]")
            for m in msgs:
                self.messages.append(ChatMessage(
                    sender=m["sender"],
                    content=m["content"] or "",
                    timestamp=format_timestamp(m["timestamp"]),
                    msg_id=m["id"],
                    is_self=(m["sender"] == self.username),
                    display_name=m.get("display_name"),
                    avatar_color=m.get("avatar_color"),
                    reactions=m.get("reactions", {}),
                    reply_to=m.get("reply_to"),
                ))
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_reply(self, parts):
        if len(parts) < 3:
            self._sys("Usage: /reply <message_id> <your reply>"); return
        try:
            msg_id = int(parts[1])
            content = parts[2]
            self._reply_to = msg_id
            await self.ws.send_message(self.current_room, content, reply_to=msg_id)
            self._reply_to = None
            self._sys(f"[dim]↩ Replied to message #{msg_id}[/dim]")
        except (ValueError, APIError) as e:
            self._sys(f"[red]✗ {e}[/red]")

    async def _cmd_edit(self, parts):
        if len(parts) < 3:
            self._sys("Usage: /edit <message_id> <new content>"); return
        try:
            msg_id = int(parts[1])
            content = parts[2]
            await self.api.patch(f"/api/v1/messages/{msg_id}", {"content": content})
            self._sys(f"[dim]✏ Message #{msg_id} edited[/dim]")
        except (ValueError, APIError) as e:
            self._sys(f"[red]✗ {e}[/red]")

    async def _cmd_delete(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /delete <message_id>"); return
        try:
            msg_id = int(parts[1])
            await self.api.delete(f"/api/v1/messages/{msg_id}")
            self._sys(f"[dim]🗑 Message #{msg_id} deleted[/dim]")
        except (ValueError, APIError) as e:
            self._sys(f"[red]✗ {e}[/red]")

    async def _cmd_react(self, parts):
        if len(parts) < 3:
            self._sys("Usage: /react <message_id> <emoji>"); return
        try:
            msg_id = int(parts[1])
            emoji = parts[2]
            await self.ws.react(msg_id, emoji)
        except ValueError:
            self._sys("[red]✗ Invalid message ID[/red]")

    async def _cmd_pin(self, parts):
        self._sys("[dim]📌 Pin feature coming soon[/dim]")

    async def _cmd_sendfile(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /sendfile <path> [username_or_room]"); return
        filepath = parts[1]
        target = parts[2] if len(parts) > 2 else None
        path = Path(filepath).expanduser()
        if not path.exists():
            self._sys(f"[red]✗ File not found: {filepath}[/red]"); return

        self._sys(f"[dim]📤 Uploading {path.name} ({format_size(path.stat().st_size)})...[/dim]")
        try:
            if target and not target.startswith("#"):
                result = await self.api.upload_file(path, recipient=target)
            else:
                room = (target or "#" + self.current_room).lstrip("#")
                result = await self.api.upload_file(path, room=room)
            self._sys(f"[green]✓ Uploaded {result['filename']} ({format_size(result['size'])})[/green]")
            self._sys(f"  [dim]File ID: {result['file_id']} | /download {result['file_id']}[/dim]")
        except APIError as e:
            self._sys(f"[red]✗ Upload failed: {e.message}[/red]")

    async def _cmd_download(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /download <file_id> [filename]"); return
        try:
            fid = int(parts[1])
            fname = parts[2] if len(parts) > 2 else f"file_{fid}"
            dest = config.download_dir / fname
            self._sys(f"[dim]📥 Downloading file #{fid}...[/dim]")

            downloaded = [0]
            def progress(done, total):
                downloaded[0] = done
                bar = progress_bar(done, total)
                self._sys(f"  {bar}", replace_last=True)

            await self.api.download_file(fid, dest, progress)
            self._sys(f"[green]✓ Saved to: {dest}[/green]")
        except (ValueError, APIError) as e:
            self._sys(f"[red]✗ {e}[/red]")

    async def _cmd_add(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /add <username>"); return
        try:
            await self.api.post(f"/api/v1/friends/add/{parts[1]}")
            self._sys(f"[green]✓ Friend request sent to {parts[1]}[/green]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_accept(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /accept <username>"); return
        try:
            await self.api.post(f"/api/v1/friends/accept/{parts[1]}")
            self._sys(f"[green]✓ Now friends with {parts[1]}! 🎉[/green]")
            await self._refresh_sidebar()
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_reject(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /reject <username>"); return
        try:
            await self.api.post(f"/api/v1/friends/reject/{parts[1]}")
            self._sys(f"[dim]Request from {parts[1]} rejected[/dim]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_friends(self):
        try:
            result = await self.api.get("/api/v1/friends/list")
            friends = result.get("friends", [])
            if not friends:
                self._sys("[dim]No friends yet. Use /add <username>[/dim]"); return
            self._sys(f"[cyan]👥 Friends ({len(friends)}):[/cyan]")
            for f in friends:
                icon = get_status_icon(f.get("status", "offline"))
                sm = f" — {f['status_message']}" if f.get("status_message") else ""
                self._sys(f"  {icon} [bold]{f['username']}[/bold] ({f.get('display_name','')}){sm}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_requests(self):
        try:
            result = await self.api.get("/api/v1/friends/requests")
            reqs = result.get("requests", [])
            if not reqs:
                self._sys("[dim]No pending requests[/dim]"); return
            self._sys(f"[cyan]📨 Friend Requests ({len(reqs)}):[/cyan]")
            for r in reqs:
                self._sys(f"  • [bold]{r['username']}[/bold] ({r.get('display_name','')}) — /accept {r['username']} | /reject {r['username']}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_users(self):
        try:
            result = await self.api.get("/api/v1/users/online")
            users = result.get("users", [])
            self._sys(f"[green]🟢 Online ({result.get('count', 0)}):[/green]")
            for u in users:
                icon = get_status_icon(u.get("status", "online"))
                sm = f" — {u['status_message']}" if u.get("status_message") else ""
                self._sys(f"  {icon} [bold]{u['username']}[/bold]{sm}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_whois(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /whois <username>"); return
        try:
            u = await self.api.get(f"/api/v1/users/{parts[1]}")
            icon = get_status_icon(u.get("status", "offline"))
            self._sys(f"[cyan]── {u['username']} ──[/cyan]")
            self._sys(f"  Name:     {u.get('display_name', u['username'])}")
            self._sys(f"  Status:   {icon} {u.get('status','offline')}")
            if u.get("status_message"):
                self._sys(f"  Message:  {u['status_message']}")
            if u.get("bio"):
                self._sys(f"  Bio:      {u['bio']}")
            self._sys(f"  Messages: {u.get('total_messages', 0)}")
            self._sys(f"  Joined:   {u.get('created_at','')[:10]}")
            if u.get("last_seen"):
                self._sys(f"  Seen:     {format_timestamp(u['last_seen'])}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_poll(self, parts):
        """Usage: /poll "Question?" Option1 | Option2 | Option3"""
        if len(parts) < 2:
            self._sys('Usage: /poll "Question?" Option1 | Option2 | Option3'); return
        raw = " ".join(parts[1:])
        if "?" in raw:
            question, rest = raw.split("?", 1)
            question = question.strip() + "?"
        else:
            self._sys('[red]✗ Poll question must end with "?"[/red]'); return

        options = [o.strip() for o in rest.split("|") if o.strip()]
        if len(options) < 2:
            self._sys("[red]✗ Need at least 2 options separated by |[/red]"); return

        try:
            result = await self.api.post("/api/v1/polls", {
                "room": self.current_room,
                "question": question,
                "options": options,
                "is_anonymous": True,
            })
            self._sys(f"[green]✓ Poll created! Vote with /vote {result['poll_id']} <option_number>[/green]")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_vote(self, parts):
        if len(parts) < 3:
            self._sys("Usage: /vote <poll_id> <option_number>"); return
        try:
            poll_id = int(parts[1])
            option = int(parts[2]) - 1
            await self.ws.vote_poll(poll_id, option)
            self._sys(f"[green]✓ Voted in poll #{poll_id}[/green]")
        except (ValueError, APIError) as e:
            self._sys(f"[red]✗ {e}[/red]")

    async def _cmd_status(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /status <online|away|busy|invisible> [message]"); return
        status = parts[1].lower()
        msg = parts[2] if len(parts) > 2 else ""
        await self._set_status(status, msg)

    async def _set_status(self, status: str, msg: str):
        try:
            await self.api.post("/api/v1/users/status", {"status": status, "status_message": msg})
            icon = get_status_icon(status)
            self._sys(f"{icon} Status set to [bold]{status}[/bold]{' — ' + msg if msg else ''}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_stats(self):
        try:
            s = await self.api.get("/api/v1/stats")
            self._sys("[cyan]── Server Stats ──[/cyan]")
            self._sys(f"  👥 Users:    {s.get('users', 0)}")
            self._sys(f"  🟢 Online:   {s.get('online_now', 0)}")
            self._sys(f"  💬 Rooms:    {s.get('rooms', 0)}")
            self._sys(f"  📨 Messages: {s.get('messages', 0)}")
            self._sys(f"  📎 Files:    {s.get('files', 0)}")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_search(self, parts):
        if len(parts) < 2:
            self._sys("Usage: /search <username>"); return
        try:
            result = await self.api.get(f"/api/v1/users/search?q={parts[1]}")
            users = result.get("users", [])
            if not users:
                self._sys(f"[dim]No users found for '{parts[1]}'[/dim]"); return
            self._sys(f"[cyan]🔍 Results for '{parts[1]}':[/cyan]")
            for u in users:
                icon = get_status_icon("online" if u.get("is_online") else "offline")
                self._sys(f"  {icon} [bold]{u['username']}[/bold] ({u.get('display_name','')})")
        except APIError as e:
            self._sys(f"[red]✗ {e.message}[/red]")

    async def _cmd_me(self, parts):
        action = " ".join(parts[1:]) if len(parts) > 1 else "is here"
        content = f"_{self.username} {action}_"
        await self.ws.send_message(self.current_room, content)

    def _show_help(self):
        help_text = [
            "[bold cyan]── Port69 v2 Commands ──[/bold cyan]",
            "",
            "[bold]Navigation[/bold]",
            "  /join <room>              Join a room",
            "  /leave                    Leave current room",
            "  /create <name> [desc]     Create a new room",
            "  /rooms                    List public rooms",
            "  /msg <user> <text>        Send a DM",
            "",
            "[bold]Messages[/bold]",
            "  /reply <id> <text>        Reply to a message",
            "  /edit <id> <text>         Edit your message",
            "  /delete <id>              Delete your message",
            "  /react <id> <emoji>       React to a message",
            "  /history [room]           Load message history",
            "  /me <action>              Send action message",
            "",
            "[bold]Files[/bold]",
            "  /sendfile <path> [target] Send a file",
            "  /download <id> [name]     Download a file",
            "",
            "[bold]Friends & Users[/bold]",
            "  /add <user>               Send friend request",
            "  /accept <user>            Accept request",
            "  /reject <user>            Reject request",
            "  /friends                  Show friends",
            "  /requests                 Pending requests",
            "  /users                    Online users",
            "  /whois <user>             User profile",
            "  /search <query>           Search users",
            "",
            "[bold]Status[/bold]",
            "  /status <online|away|busy|invisible> [msg]",
            "  /away  /busy  /back       Quick status",
            "",
            "[bold]Polls[/bold]",
            '  /poll "Question?" A | B | C   Create poll',
            "  /vote <poll_id> <number>       Vote in poll",
            "",
            "[bold]Other[/bold]",
            "  /stats                    Server statistics",
            "  /clear                    Clear chat",
            "  /quit                     Exit",
            "",
            "[bold]Markdown:[/bold] **bold** *italic* `code` ~~strike~~ @mention #room",
        ]
        for line in help_text:
            self._sys(line)

    # ── WEBSOCKET HANDLER ──────────────────────────────────────────

    async def _on_ws(self, msg: dict):
        t = msg.get("type")

        if t == "message":
            if msg.get("sender") != self.username:
                file_info = msg.get("file")
                self.messages.append(ChatMessage(
                    sender=msg["sender"],
                    content=msg.get("content", ""),
                    timestamp=format_timestamp(msg.get("timestamp", "")),
                    msg_id=msg.get("id", 0),
                    is_self=False,
                    msg_type=msg.get("message_type", "text"),
                    reply_to=msg.get("reply_to"),
                    avatar_color=msg.get("avatar_color"),
                    file_info=file_info,
                ))
                self._notify(f"💬 {msg['sender']}: {(msg.get('content',''))[:40]}")

        elif t == "poll":
            self._polls[msg["poll_id"]] = msg
            self.messages.append(ChatMessage(
                sender=msg["sender"],
                content=f"📊 Poll: {msg['question']}\n" + "\n".join(f"  {i+1}. {o}" for i, o in enumerate(msg.get("options", []))),
                timestamp=format_timestamp(msg.get("timestamp", "")),
                msg_id=msg.get("message_id", 0),
                msg_type="poll",
            ))
            self._notify(f"📊 {msg['sender']} created a poll: {msg['question'][:40]}")

        elif t == "poll_update":
            pid = msg.get("poll_id")
            if pid in self._polls:
                self._polls[pid]["vote_counts"] = msg.get("vote_counts", {})
                total = msg.get("total_votes", 0)
                self._sys(f"[dim]📊 Poll #{pid}: {total} vote(s) so far[/dim]")

        elif t == "system":
            self.messages.append(ChatMessage(
                sender="system", content=msg.get("content", ""),
                timestamp=format_timestamp(msg.get("timestamp", "")),
                is_system=True,
            ))

        elif t == "typing":
            uname = msg.get("username")
            if msg.get("is_typing"):
                self.typing_users.add(uname)
            else:
                self.typing_users.discard(uname)

        elif t == "presence":
            uname = msg.get("username")
            status = msg.get("status", "offline")
            if status == "online":
                self._notify(f"🟢 {uname} came online")
            elif status == "offline":
                self._notify(f"⚫ {uname} went offline")
            # Update online users
            await self._refresh_online()

        elif t == "reaction":
            mid = msg.get("message_id")
            emoji = msg.get("emoji")
            action = msg.get("action", "add")
            uname = msg.get("username", "?")
            for m in self.messages:
                if m.msg_id == mid:
                    if action == "add":
                        m.reactions[emoji] = m.reactions.get(emoji, 0) + 1
                    else:
                        if emoji in m.reactions:
                            m.reactions[emoji] = max(0, m.reactions[emoji] - 1)
                            if m.reactions[emoji] == 0:
                                del m.reactions[emoji]
                    break

        elif t == "message_deleted":
            mid = msg.get("message_id")
            for m in self.messages:
                if m.msg_id == mid:
                    m.content = "[deleted]"
                    m.is_system = True
                    break

        elif t == "message_edited":
            mid = msg.get("message_id")
            for m in self.messages:
                if m.msg_id == mid:
                    m.content = msg.get("content", m.content)
                    m.is_edited = True
                    break

        elif t == "friend_request":
            self._notify(f"👥 Friend request from {msg.get('from')}")
            self._sys(f"[yellow]👥 Friend request from [bold]{msg.get('from')}[/bold] — /accept {msg.get('from')}[/yellow]")

        elif t == "friend_accepted":
            self._notify(f"✅ {msg.get('by')} accepted your request!")
            self._sys(f"[green]✅ [bold]{msg.get('by')}[/bold] is now your friend![/green]")
            await self._refresh_sidebar()

        elif t == "notification":
            content = msg.get("content", {})
            if isinstance(content, dict):
                preview = content.get("preview", "")
                sender = content.get("sender", "?")
                self._notify(f"📩 {sender}: {preview[:40]}")

        elif t == "connected":
            self._notify(f"🌐 Connected — {msg.get('online_count', 0)} online")

    # ── RENDERING ─────────────────────────────────────────────────

    def _render(self):
        os.system("clear" if os.name != "nt" else "cls")
        self._render_header()
        self._render_notifications()
        self._render_main()
        self._render_footer()

    def _render_header(self):
        room_label = self._room_label()
        online = len([u for u in self.online_users])

        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(justify="center", ratio=2)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            f"[bold bright_green]⚡ Port69[/bold bright_green] [dim]v2[/dim]",
            f"[bold white]{room_label}[/bold white]",
            f"[green]●[/green] [cyan]{self.username}[/cyan]  [dim]{online} online[/dim]",
        )
        console.print(Panel(grid, style="on black", border_style="bright_black", padding=(0, 1)))

    def _render_notifications(self):
        if self.notifications:
            recent = list(self.notifications)[-2:]
            parts = "  [dim bright_black]│[/dim bright_black]  ".join(
                f"[dim]{n['time']}[/dim] {n['text']}" for n in recent
            )
            console.print(f" {parts}")

    def _render_main(self):
        chat = self._build_chat()
        sidebar = self._build_sidebar()

        layout = Table.grid(expand=True)
        layout.add_column(ratio=5)
        layout.add_column(ratio=1, min_width=24)
        layout.add_row(chat, sidebar)
        console.print(layout)

    def _build_chat(self) -> Panel:
        lines = []
        prev_sender = None
        shown = list(self.messages)[-35:]

        for msg in shown:
            if msg.is_system:
                lines.append(f"[dim italic cyan]  ─ {escape(msg.content)}[/dim italic cyan]")
                prev_sender = None
                continue

            show_header = (msg.sender != prev_sender)
            prev_sender = msg.sender

            if show_header:
                color = "bright_white" if msg.is_self else get_user_color(msg.sender)
                avatar = make_avatar(msg.sender, msg.avatar_color)
                name = f"[bold {color}]{escape(msg.display_name or msg.sender)}[/bold {color}]"
                edited = " [dim](edited)[/dim]" if msg.is_edited else ""
                lines.append(f"\n  {avatar} {name}  [dim]{msg.timestamp}[/dim]{edited}")

            # Reply context
            if msg.reply_to:
                rpreview = (msg.reply_to.get("content") or "")[:60]
                rname = msg.reply_to.get("sender", "?")
                lines.append(f"  [dim bright_black]  ╭ ↩ {rname}: {escape(rpreview)}[/dim bright_black]")

            # Content
            if msg.msg_type == "file":
                fi = msg.file_info or {}
                lines.append(f"  [cyan]  📎 {escape(fi.get('filename', msg.content))} ({format_size(fi.get('size', 0))})[/cyan]")
                if fi.get("id"):
                    lines.append(f"  [dim]     /download {fi['id']} to save[/dim]")
            elif msg.msg_type == "poll":
                lines.append(f"  [yellow]  {escape(msg.content)}[/yellow]")
            else:
                rendered = render_content(escape(msg.content))
                lines.append(f"  {'  ' if msg.is_self else '  '}{rendered}")

            # Reactions
            if msg.reactions:
                rxn = "  " + " ".join(f"{e}[dim]{c}[/dim]" for e, c in msg.reactions.items())
                lines.append(rxn)

        content = "\n".join(lines) if lines else "[dim]  No messages yet. Say hello! 👋[/dim]"
        return Panel(content, border_style="bright_black", height=26, title=f"[dim]{self._room_label()}[/dim]")

    def _build_sidebar(self) -> Panel:
        lines = []

        # Friends
        if self.friends:
            lines.append("[bold cyan]Friends[/bold cyan]")
            for f in self.friends[:8]:
                icon = get_status_icon(f.get("status", "offline"))
                name = (f.get("display_name") or f.get("username", "?"))[:16]
                lines.append(f" {icon} {name}")
            lines.append("")

        # My rooms
        if self.my_rooms:
            lines.append("[bold cyan]Rooms[/bold cyan]")
            for r in self.my_rooms[:6]:
                icon = r.get("icon", "💬")
                name = r.get("name", "?")[:14]
                active = "[bold bright_green]" if name == self.current_room else ""
                end = "[/bold bright_green]" if active else ""
                lines.append(f" {icon} {active}#{name}{end}")
            lines.append("")

        # Online
        lines.append("[bold green]Online[/bold green]")
        shown_names = {f.get("username") for f in self.friends}
        others = [u for u in self.online_users if u.get("username") not in shown_names and u.get("username") != self.username]
        for u in others[:5]:
            lines.append(f" [green]●[/green] {u.get('username','?')[:16]}")

        if not lines:
            lines = ["[dim]No one online[/dim]"]

        return Panel("\n".join(lines), border_style="bright_black", title="[dim]People[/dim]")

    def _render_footer(self):
        typing = ""
        if self.typing_users:
            names = ", ".join(list(self.typing_users)[:2])
            typing = f" [dim italic]{names} typing...[/dim italic] │"

        hints = [
            "/help", "/join", "/msg", "/add",
            "/sendfile", "/react", "/poll", "/stats", "Ctrl+C quit"
        ]
        hint_str = " [dim bright_black]·[/dim bright_black] ".join(f"[dim]{h}[/dim]" for h in hints)
        console.print(Panel(f"{typing} {hint_str}", border_style="bright_black", padding=(0, 1)))

    # ── HELPERS ───────────────────────────────────────────────────

    def _room_label(self) -> str:
        if self.current_room.startswith("dm_"):
            parts = self.current_room[3:].split("_")
            partner = next((p for p in parts if p != self.username), parts[0])
            return f"[magenta]@{partner}[/magenta]"
        return f"[cyan]#{self.current_room}[/cyan]"

    def _sys(self, text: str, replace_last: bool = False):
        if replace_last and self.messages:
            last = self.messages[-1]
            if last.is_system:
                self.messages.pop()
        self.messages.append(ChatMessage(
            sender="system", content=text,
            timestamp=datetime.now().strftime("%H:%M"),
            is_system=True,
        ))

    def _notify(self, text: str):
        self.notifications.append({"text": text, "time": datetime.now().strftime("%H:%M")})

    def add_system(self, text: str, replace_last: bool = False):
        self._sys(text, replace_last)

    def clear_messages(self):
        self.messages.clear()

    async def _load_history(self):
        try:
            result = await self.api.get(f"/api/v1/messages/history/{self.current_room}?limit=40")
            for m in result.get("messages", []):
                self.messages.append(ChatMessage(
                    sender=m["sender"],
                    content=m.get("content") or "",
                    timestamp=format_timestamp(m["timestamp"]),
                    msg_id=m.get("id", 0),
                    is_self=(m["sender"] == self.username),
                    display_name=m.get("display_name"),
                    avatar_color=m.get("avatar_color"),
                    reactions=m.get("reactions", {}),
                    reply_to=m.get("reply_to"),
                    msg_type=m.get("message_type", "text"),
                ))
        except APIError:
            pass

    async def _refresh_sidebar(self):
        await self._refresh_friends()
        await self._refresh_rooms()
        await self._refresh_online()

    async def _refresh_friends(self):
        try:
            r = await self.api.get("/api/v1/friends/list")
            self.friends = r.get("friends", [])
        except APIError:
            pass

    async def _refresh_rooms(self):
        try:
            r = await self.api.get("/api/v1/rooms/my")
            self.my_rooms = r.get("rooms", [])
        except APIError:
            pass

    async def _refresh_online(self):
        try:
            r = await self.api.get("/api/v1/users/online")
            self.online_users = r.get("users", [])
        except APIError:
            pass

    async def _periodic_refresh(self):
        while self._running:
            await asyncio.sleep(30)
            await self._refresh_sidebar()

    async def _typing_cleanup(self):
        """Auto-clear stale typing indicators."""
        while self._running:
            await asyncio.sleep(6)
            self.typing_users.clear()
