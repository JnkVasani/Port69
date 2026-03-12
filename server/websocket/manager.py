"""Port69 v2 - WebSocket Manager"""
import json
import asyncio
from datetime import datetime
from typing import Dict, Set, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from server.database.db import (
    AsyncSessionLocal, User, Message, Room, RoomMember,
    MessageType, Notification, Reaction, Poll, PollVote, AuditLog
)
from server.auth.auth import get_user_from_token
from sqlalchemy import select, and_, func

router = APIRouter()


class ConnectionManager:
    def __init__(self):
        self.active: Dict[int, WebSocket] = {}           # user_id → ws
        self.rooms: Dict[str, Set[int]] = {}             # room → user_ids
        self.user_rooms: Dict[int, Set[str]] = {}        # user_id → rooms
        self.typing: Dict[str, Dict[int, float]] = {}    # room → {user_id: timestamp}
        self._typing_cleanup_task = None

    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        self.active[user_id] = ws
        self.user_rooms.setdefault(user_id, set())

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user:
                from server.database.db import UserStatus
                user.status = UserStatus.ONLINE
                user.last_seen = datetime.utcnow()
                await db.commit()
                await self._broadcast_presence(user.username, "online", db)

    async def disconnect(self, user_id: int):
        self.active.pop(user_id, None)
        for room in list(self.user_rooms.get(user_id, [])):
            self.rooms.get(room, set()).discard(user_id)
            self.typing.get(room, {}).pop(user_id, None)
        self.user_rooms.pop(user_id, None)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user:
                from server.database.db import UserStatus
                user.status = UserStatus.OFFLINE
                user.last_seen = datetime.utcnow()
                await db.commit()
                await self._broadcast_presence(user.username, "offline", db)

    def subscribe(self, user_id: int, room: str):
        self.rooms.setdefault(room, set()).add(user_id)
        self.user_rooms.setdefault(user_id, set()).add(room)

    def unsubscribe(self, user_id: int, room: str):
        self.rooms.get(room, set()).discard(user_id)
        self.user_rooms.get(user_id, set()).discard(room)

    async def send(self, user_id: int, msg: dict):
        ws = self.active.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(msg, default=str))
            except Exception:
                pass

    async def broadcast(self, room: str, msg: dict, exclude: Optional[int] = None):
        for uid in list(self.rooms.get(room, [])):
            if uid != exclude:
                await self.send(uid, msg)

    async def broadcast_all(self, msg: dict, exclude: Optional[int] = None):
        for uid in list(self.active.keys()):
            if uid != exclude:
                await self.send(uid, msg)

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active

    def online_count(self) -> int:
        return len(self.active)

    def online_in_room(self, room: str) -> int:
        return len(self.rooms.get(room, set()))

    async def _broadcast_presence(self, username: str, status: str, db):
        msg = {
            "type": "presence",
            "username": username,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
        }
        await self.broadcast_all(msg)

    def set_typing(self, room: str, user_id: int):
        self.typing.setdefault(room, {})[user_id] = datetime.utcnow().timestamp()

    def clear_typing(self, room: str, user_id: int):
        self.typing.get(room, {}).pop(user_id, None)

    def get_typing(self, room: str) -> list:
        now = datetime.utcnow().timestamp()
        active = {uid for uid, ts in self.typing.get(room, {}).items() if now - ts < 5}
        self.typing[room] = {uid: ts for uid, ts in self.typing.get(room, {}).items() if uid in active}
        return list(active)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default=None)):
    if not token:
        await websocket.close(code=4001, reason="No token")
        return

    async with AsyncSessionLocal() as db:
        user = await get_user_from_token(token, db)
        if not user:
            await websocket.close(code=4001, reason="Invalid token")
            return
        user_id, username = user.id, user.username

    await manager.connect(user_id, websocket)

    # Auto-subscribe to user's rooms and deliver offline messages
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Room).join(RoomMember).where(RoomMember.user_id == user_id)
        )
        for room in result.scalars().all():
            manager.subscribe(user_id, room.name)

        await _deliver_offline(user_id, db)

    await manager.send(user_id, {
        "type": "connected",
        "username": username,
        "online_count": manager.online_count(),
        "timestamp": datetime.utcnow().isoformat(),
    })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                await _route(user_id, username, msg)
            except json.JSONDecodeError:
                await manager.send(user_id, {"type": "error", "message": "Invalid JSON"})
    except WebSocketDisconnect:
        await manager.disconnect(user_id)


async def _route(user_id: int, username: str, msg: dict):
    t = msg.get("type")
    handlers = {
        "chat": _handle_chat,
        "join_room": _handle_join,
        "leave_room": _handle_leave,
        "typing_start": _handle_typing_start,
        "typing_stop": _handle_typing_stop,
        "react": _handle_react,
        "unreact": _handle_unreact,
        "poll_vote": _handle_poll_vote,
        "read": _handle_read,
        "ping": lambda uid, uname, m: manager.send(uid, {"type": "pong"}),
    }
    handler = handlers.get(t)
    if handler:
        await handler(user_id, username, msg)
    else:
        await manager.send(user_id, {"type": "error", "message": f"Unknown type: {t}"})


async def _handle_chat(user_id: int, username: str, msg: dict):
    room_name = msg.get("room", "").strip()
    content = msg.get("content", "").strip()
    if not room_name or not content:
        return
    if len(content) > 4000:
        await manager.send(user_id, {"type": "error", "message": "Message too long (max 4000 chars)"})
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Room).join(RoomMember).where(
                and_(Room.name == room_name, RoomMember.user_id == user_id)
            )
        )
        room = result.scalar_one_or_none()
        if not room:
            await manager.send(user_id, {"type": "error", "message": f"Not a member of #{room_name}"})
            return

        if room.is_read_only:
            result2 = await db.execute(
                select(RoomMember).where(and_(RoomMember.room_id == room.id, RoomMember.user_id == user_id))
            )
            m = result2.scalar_one_or_none()
            if not m or not m.is_admin:
                await manager.send(user_id, {"type": "error", "message": "Room is read-only"})
                return

        reply_to_id = msg.get("reply_to")
        message = Message(
            room_id=room.id,
            sender_id=user_id,
            content=content,
            message_type=MessageType.TEXT,
            is_encrypted=msg.get("encrypted", False),
            reply_to_id=reply_to_id,
        )
        db.add(message)

        # Update counters
        result3 = await db.execute(select(User).where(User.id == user_id))
        sender = result3.scalar_one_or_none()
        if sender:
            sender.total_messages = (sender.total_messages or 0) + 1
        room.total_messages = (room.total_messages or 0) + 1

        await db.flush()

        # Offline notifications
        members_result = await db.execute(select(RoomMember).where(RoomMember.room_id == room.id))
        for member in members_result.scalars().all():
            if member.user_id != user_id and not manager.is_online(member.user_id) and member.notifications:
                db.add(Notification(
                    user_id=member.user_id,
                    type="message",
                    content=json.dumps({
                        "room": room_name,
                        "sender": username,
                        "preview": content[:80],
                        "message_id": message.id,
                    }),
                ))
        await db.commit()

        # Get reply context
        reply_data = None
        if reply_to_id:
            r = await db.execute(select(Message).where(Message.id == reply_to_id))
            rm = r.scalar_one_or_none()
            if rm:
                ru = await db.execute(select(User).where(User.id == rm.sender_id))
                ruser = ru.scalar_one_or_none()
                reply_data = {
                    "id": rm.id,
                    "sender": ruser.username if ruser else "?",
                    "content": (rm.content or "")[:100],
                }

    manager.clear_typing(room_name, user_id)

    await manager.broadcast(room_name, {
        "type": "message",
        "id": message.id,
        "room": room_name,
        "sender": username,
        "content": content,
        "message_type": "text",
        "encrypted": msg.get("encrypted", False),
        "reply_to": reply_data,
        "timestamp": message.created_at.isoformat(),
    })


async def _handle_join(user_id: int, username: str, msg: dict):
    room = msg.get("room")
    if room:
        manager.subscribe(user_id, room)
        await manager.send(user_id, {"type": "joined_room", "room": room})


async def _handle_leave(user_id: int, username: str, msg: dict):
    room = msg.get("room")
    if room:
        manager.unsubscribe(user_id, room)


async def _handle_typing_start(user_id: int, username: str, msg: dict):
    room = msg.get("room")
    if room:
        manager.set_typing(room, user_id)
        await manager.broadcast(room, {
            "type": "typing",
            "room": room,
            "username": username,
            "is_typing": True,
        }, exclude=user_id)


async def _handle_typing_stop(user_id: int, username: str, msg: dict):
    room = msg.get("room")
    if room:
        manager.clear_typing(room, user_id)
        await manager.broadcast(room, {
            "type": "typing",
            "room": room,
            "username": username,
            "is_typing": False,
        }, exclude=user_id)


async def _handle_react(user_id: int, username: str, msg: dict):
    message_id = msg.get("message_id")
    emoji = msg.get("emoji", "")
    if not message_id or not emoji:
        return

    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(Reaction).where(and_(
                Reaction.message_id == message_id,
                Reaction.user_id == user_id,
                Reaction.emoji == emoji
            ))
        )
        if not existing.scalar_one_or_none():
            db.add(Reaction(message_id=message_id, user_id=user_id, emoji=emoji))
            await db.commit()

        result = await db.execute(select(Message).where(Message.id == message_id))
        m = result.scalar_one_or_none()
        if m:
            room_result = await db.execute(select(Room).where(Room.id == m.room_id))
            room = room_result.scalar_one_or_none()
            if room:
                await manager.broadcast(room.name, {
                    "type": "reaction",
                    "message_id": message_id,
                    "emoji": emoji,
                    "username": username,
                    "action": "add",
                })


async def _handle_unreact(user_id: int, username: str, msg: dict):
    message_id = msg.get("message_id")
    emoji = msg.get("emoji", "")
    if not message_id or not emoji:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Reaction).where(and_(
                Reaction.message_id == message_id,
                Reaction.user_id == user_id,
                Reaction.emoji == emoji
            ))
        )
        reaction = result.scalar_one_or_none()
        if reaction:
            await db.delete(reaction)
            await db.commit()

            m_result = await db.execute(select(Message).where(Message.id == message_id))
            m = m_result.scalar_one_or_none()
            if m:
                room_result = await db.execute(select(Room).where(Room.id == m.room_id))
                room = room_result.scalar_one_or_none()
                if room:
                    await manager.broadcast(room.name, {
                        "type": "reaction",
                        "message_id": message_id,
                        "emoji": emoji,
                        "username": username,
                        "action": "remove",
                    })


async def _handle_poll_vote(user_id: int, username: str, msg: dict):
    poll_id = msg.get("poll_id")
    option_index = msg.get("option_index")
    if poll_id is None or option_index is None:
        return

    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(PollVote).where(and_(PollVote.poll_id == poll_id, PollVote.user_id == user_id))
        )
        if not existing.scalar_one_or_none():
            db.add(PollVote(poll_id=poll_id, user_id=user_id, option_index=option_index))
            await db.commit()

        poll_result = await db.execute(select(Poll).where(Poll.id == poll_id))
        poll = poll_result.scalar_one_or_none()
        if poll:
            votes = await db.execute(select(PollVote).where(PollVote.poll_id == poll_id))
            all_votes = votes.scalars().all()
            counts = {}
            for v in all_votes:
                counts[v.option_index] = counts.get(v.option_index, 0) + 1

            m_result = await db.execute(select(Message).where(Message.id == poll.message_id))
            m = m_result.scalar_one_or_none()
            if m:
                room_result = await db.execute(select(Room).where(Room.id == m.room_id))
                room = room_result.scalar_one_or_none()
                if room:
                    await manager.broadcast(room.name, {
                        "type": "poll_update",
                        "poll_id": poll_id,
                        "vote_counts": counts,
                        "total_votes": len(all_votes),
                        "voter": username if not poll.is_anonymous else None,
                    })


async def _handle_read(user_id: int, username: str, msg: dict):
    room_name = msg.get("room")
    message_id = msg.get("message_id")
    if not room_name or not message_id:
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RoomMember).join(Room).where(
                and_(Room.name == room_name, RoomMember.user_id == user_id)
            )
        )
        member = result.scalar_one_or_none()
        if member:
            member.last_read_message_id = message_id
            await db.commit()


async def _deliver_offline(user_id: int, db):
    from sqlalchemy import and_
    result = await db.execute(
        select(Notification).where(
            and_(Notification.user_id == user_id, Notification.is_read == False)
        )
    )
    notifications = result.scalars().all()
    for n in notifications:
        await manager.send(user_id, {
            "type": "notification",
            "notification_type": n.type,
            "content": json.loads(n.content),
            "timestamp": n.created_at.isoformat(),
        })
        n.is_read = True
    if notifications:
        await db.commit()
