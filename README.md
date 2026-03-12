# ⚡ Port69

**Terminal chat platform. Talk to friends. Share files. No browser needed.**

```
pip install port69
```

---

## What is Port69?

Port69 is an open-source CLI chat app — like WhatsApp or Discord, but fully in your terminal. Chat in rooms, send DMs, share files, react to messages, create polls, and more.

```
┌─────────────────────────────────────────────┬────────────────┐
│  ⚡ Port69 v1.0            #general   ● janak│ Friends        │
├─────────────────────────────────────────────│ ● rohan        │
│                                             │ ● priya        │
│  J  janak  14:02                           │                │
│     Hey everyone! 👋                       │ Rooms          │
│     👍2  ❤️1                               │ 💬 #general    │
│                                             │ 🎮 #gaming     │
│  R  rohan  14:03                           │ 💻 #code       │
│     ↩ janak: Hey everyone!                 │                │
│     Sup! Just joined                        │ Online         │
│                                             │ ● alex         │
│  📊 Poll: Best language?                   │ ● sam          │
│     1. Python  2. Go  3. Rust              │                │
│     👍 12 votes                            │                │
├─────────────────────────────────────────────┴────────────────┤
│  /help · /join · /msg · /add · /sendfile · /poll  Ctrl+C quit│
└──────────────────────────────────────────────────────────────┘
#general ❯ 
```

---

## Quick Start (User)

**1. Install**
```bash
pip install port69
```

**2. Connect to a server**
```bash
port69 config
# Enter server URL when asked:  https://your-friends-server.com
```

**3. Register**
```bash
port69 register
```

**4. Chat!**
```bash
port69 chat
```

---

## Self-Host Your Own Server

So your friends can connect to **you**:

```bash
# Install server dependencies
pip install "port69[server]"

# Create config
echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
echo "DATABASE_URL=sqlite+aiosqlite:///./port69.db" >> .env

mkdir uploads

# Start server
port69-server
```

Server runs on `http://0.0.0.0:8000`

**Friends connect to you:**
```bash
port69 config
# Server URL: http://YOUR_IP:8000
port69 register
port69 chat
```

---

## All Commands

### In Terminal
```bash
port69 register          # Create account
port69 login             # Login
port69 chat              # Open chat (main room)
port69 chat <username>   # Open DM with someone
port69 friends           # Your friends list
port69 users             # Who's online
port69 rooms             # Public rooms
port69 stats             # Server statistics
port69 whois <username>  # View someone's profile
port69 add <username>    # Send friend request
port69 profile           # Edit your profile
port69 config            # Change server / settings
```

### Inside Chat
```
Messages
  /reply <id> <text>       Reply to a message
  /edit  <id> <text>       Edit your message
  /delete <id>             Delete your message
  /react <id> 👍           React with emoji
  /me <action>             /me waves hello

Navigation
  /join <room>             Join a room
  /leave                   Leave current room
  /create <name> [desc]    Create a new room
  /rooms                   List all public rooms
  /msg <user> <text>       Send a direct message

Files
  /sendfile <path>         Send a file to current room
  /sendfile <path> <user>  Send a file to a specific person
  /download <id>           Download a file

Friends
  /add <user>              Send friend request
  /accept <user>           Accept a request
  /reject <user>           Reject a request
  /friends                 Show friends
  /requests                Pending requests

Polls
  /poll "Question?" A | B | C    Create a poll
  /vote <poll_id> <number>        Vote in a poll

Status
  /status away Grabbing coffee    Set custom status
  /away  /busy  /back             Quick status shortcuts

Other
  /users                   Who's online
  /whois <user>            User profile
  /search <name>           Find users
  /stats                   Server stats
  /clear                   Clear chat
  /help                    Show all commands
  /quit                    Exit
```

---

## Markdown in Chat

```
**bold**        *italic*        `code`
~~strikethrough~~
@username       #roomname
https://links   automatically become clickable
```

---

## Features

- 💬 **Real-time messaging** — WebSocket powered, instant delivery
- 📁 **File sharing** — Send any file type up to 200MB
- 👥 **Friends system** — Add friends, accept/reject requests
- 📊 **Polls** — Create polls with live vote counts
- 🔔 **Notifications** — Offline message delivery when you reconnect
- 😀 **Reactions** — React to any message with any emoji
- ↩️ **Reply threads** — Reply to specific messages with quote preview
- ✏️ **Edit & delete** — Edit or delete your own messages
- 🟢 **Presence** — Online/Away/Busy/Invisible status
- 🌐 **Multi-room** — Create and join multiple rooms
- 🔍 **User search** — Find anyone on the server
- 🎨 **Rich UI** — Colors, avatars, markdown rendering

---

## Deploy to the Cloud (Render — Free)

1. Push your code to GitHub
2. Go to [render.com](https://render.com) → New Web Service
3. Connect your repo
4. Set:
   - **Build command:** `pip install ".[server]"`
   - **Start command:** `port69-server`
   - **Environment variables:** `SECRET_KEY`, `DATABASE_URL`
5. Deploy!

Share your Render URL with friends — they install port69 and connect to you.

---

## Docker

```bash
docker compose up -d
```

---

## License

MIT — free to use, modify, and distribute.

---

*Built with FastAPI · SQLAlchemy · WebSockets · Rich · Click*
