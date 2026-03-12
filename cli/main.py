"""Port69 v2 - CLI Entry Point"""
import click
import asyncio
from cli.config import config
from cli.ui.theme import print_banner, print_error, print_info, print_success, console


def require_auth():
    if not config.is_authenticated():
        print_error("Not logged in. Run [bold]port69 login[/bold] first.")
        raise SystemExit(1)


@click.group()
@click.version_option("2.0.0", prog_name="Port69")
def cli():
    """⚡ Port69 v2 — Terminal Communication Platform\n\nChat globally from your command line."""
    pass


@cli.command()
def register():
    """Create a new Port69 account."""
    from cli.commands.auth import register_command
    asyncio.run(register_command())


@cli.command()
def login():
    """Login to your Port69 account."""
    from cli.commands.auth import login_command
    asyncio.run(login_command())


@cli.command()
def logout():
    """Logout from Port69."""
    from cli.commands.auth import logout_command
    logout_command()


@cli.command()
@click.argument("username", required=False)
def chat(username):
    """Open the chat interface. Optionally DM a specific user."""
    require_auth()
    from cli.ui.chat_ui import ChatUI
    ui = ChatUI(username=config.username, target=username)
    asyncio.run(ui.run())


@cli.command()
@click.argument("username")
def add(username):
    """Send a friend request."""
    require_auth()
    from cli.commands.social import cmd_add
    asyncio.run(cmd_add(username))


@cli.command()
def friends():
    """Show your friends list."""
    require_auth()
    from cli.commands.social import cmd_friends
    asyncio.run(cmd_friends())


@cli.command()
def requests():
    """Show pending friend requests."""
    require_auth()
    from cli.commands.social import cmd_requests
    asyncio.run(cmd_requests())


@cli.command()
def users():
    """Show online users."""
    require_auth()
    from cli.commands.social import cmd_users
    asyncio.run(cmd_users())


@cli.command()
def stats():
    """Show server statistics."""
    require_auth()
    from cli.commands.social import cmd_stats
    asyncio.run(cmd_stats())


@cli.command()
def profile():
    """View and edit your profile."""
    require_auth()
    from cli.commands.auth import profile_command
    asyncio.run(profile_command())


@cli.command()
def config_cmd():
    """Configure Port69 settings."""
    from cli.commands.auth import configure_command
    configure_command()


# Rename to avoid Python keyword clash
cli.add_command(config_cmd, name="config")


@cli.command()
@click.argument("username")
def whois(username):
    """Look up a user's profile."""
    require_auth()
    from cli.commands.social import cmd_whois
    asyncio.run(cmd_whois(username))


@cli.command()
def rooms():
    """List available public rooms."""
    require_auth()
    from cli.commands.social import cmd_rooms
    asyncio.run(cmd_rooms())


def main():
    cli()


if __name__ == "__main__":
    main()
