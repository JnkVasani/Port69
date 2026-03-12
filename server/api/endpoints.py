"""Port69 v2 - All API Endpoints"""
import os
import uuid
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, desc, func
from pydantic import BaseModel, validator

from server.database.db import (
    get_db, User, Room, RoomMember, Message, FileUpload,
    Friendship, FriendStatus, Notification, Reaction, Poll,
    PollVote, MessageType, RoomType, UserStatus, AuditLog
)
from server.auth.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_admin_user
)
from server.websocket.manager import manager
from server.config import settings

router = APIRouter()

# ─── SCHEMAS ──────────────────────────────────────────────────────

class RegisterReq(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    display_name: Optional[str] = None

    @validator("username")
    def validate_username(cls, v):
        v = v.strip().lower()
        if not v.replace("_","").replace("-","").isalnum():
            raise ValueError("Only letters, numbers, underscores, hyphens")
        if not 3 <= len(v) <= 32:
            raise ValueError("Must be 3-32 characters")
        return v

    @validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Must be at least 8 characters")
        return v


class LoginReq(BaseModel):
    username: str
    password: str


class UpdateProfileReq(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    status_message: Optional[str] = None
    public_key: Optional[str] = None
    avatar_color: Optional[str] = None
    theme: Optional[str] = None
    notification_sound: Optional[bool] = None


class CreateRoomReq(BaseModel):
    name: str
    description: Optional[str] = None
    display_name: Optional[str] = None
    icon: Optional[str] = "💬"
    is_private: bool = False
    topic: Optional[str] = None
    tags: Optional[List[str]] = None


class CreatePollReq(BaseModel):
    room: str
    question: str
    options: List[str]
    is_multiple: bool = False
    is_anonymous: bool = True


class SendAnnouncementReq(BaseModel):
    room: str
    content: str


class SetStatusReq(BaseModel):
    status: str
    status_message: Optional[str] = None


# ─── USERS ────────────────────────────────────────────────────────

@router.post("/users/register", status_code=201, tags=["auth"])
async def register(req: RegisterReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(400, "Username already taken")

    if req.email:
        result = await db.execute(select(User).where(User.email == req.email))
        if result.scalar_one_or_none():
            raise HTTPException(400, "Email already registered")

    import random
    colors = ["#00ff88","#ff6b6b","#4ecdc4","#45b7d1","#96ceb4","#ffad8e","#dda0dd","#98d8c8"]
    user = User(
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        display_name=req.display_name or req.username,
        avatar_color=random.choice(colors),
    )
    db.add(user)
    await db.flush()

    # Auto-join general room
    result = await db.execute(select(Room).where(Room.name == "general"))
    general = result.scalar_one_or_none()
    if not general:
        general = Room(name="general", display_name="General", description="Welcome to Port69!", icon="👋")
        db.add(general)
        await db.flush()
    db.add(RoomMember(room_id=general.id, user_id=user.id))
    await db.commit()

    token = create_access_token({"sub": user.username})
    return {"message": "Welcome to Port69!", "username": user.username, "token": token, "avatar_color": user.avatar_color}


@router.post("/users/login", tags=["auth"])
async def login(req: LoginReq, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username.lower().strip()))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "Invalid username or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled")

    token = create_access_token({"sub": user.username})
    return {
        "message": "Login successful",
        "username": user.username,
        "display_name": user.display_name,
        "avatar_color": user.avatar_color,
        "token": token,
    }


@router.get("/users/me", tags=["users"])
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "display_name": current_user.display_name,
        "email": current_user.email,
        "bio": current_user.bio,
        "avatar_color": current_user.avatar_color,
        "status": current_user.status.value if current_user.status else "offline",
        "status_message": current_user.status_message,
        "theme": current_user.theme,
        "total_messages": current_user.total_messages,
        "created_at": current_user.created_at.isoformat(),
        "is_admin": current_user.is_admin,
    }


@router.patch("/users/me", tags=["users"])
async def update_profile(
    req: UpdateProfileReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    for field, val in req.dict(exclude_none=True).items():
        setattr(current_user, field, val)
    await db.commit()
    return {"message": "Profile updated"}


@router.post("/users/status", tags=["users"])
async def set_status(
    req: SetStatusReq,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status_map = {
        "online": UserStatus.ONLINE,
        "away": UserStatus.AWAY,
        "busy": UserStatus.BUSY,
        "invisible": UserStatus.INVISIBLE,
    }
    s = status_map.get(req.status.lower())
    if not s:
        raise HTTPException(400, f"Invalid status. Choose: {list(status_map.keys())}")
    current_user.status = s
    if req.status_message is not None:
        current_user.status_message = req.status_message
    await db.commit()

    await manager.broadcast_all({
        "type": "presence",
        "username": current_user.username,
        "status": req.status,
        "status_message": req.status_message,
        "timestamp": datetime.utcnow().isoformat(),
    })
    return {"message": f"Status set to {req.status}"}


@router.get("/users/online", tags=["users"])
async def online_users(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.status != UserStatus.OFFLINE))
    users = result.scalars().all()
    return {
        "users": [
            {
                "username": u.username,
                "display_name": u.display_name,
                "avatar_color": u.avatar_color,
                "status": u.status.value if u.status else "offline",
                "status_message": u.status_message,
                "is_online": manager.is_online(u.id),
            }
            for u in users if manager.is_online(u.id)
        ],
        "count": manager.online_count(),
    }


@router.get("/users/search", tags=["users"])
async def search_users(
    q: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            and_(
                User.is_active == True,
                or_(
                    User.username.ilike(f"%{q}%"),
                    User.display_name.ilike(f"%{q}%"),
                )
            )
        ).limit(20)
    )
    users = result.scalars().all()
    return {"users": [{"username": u.username, "display_name": u.display_name, "avatar_color": u.avatar_color, "is_online": manager.is_online(u.id)} for u in users]}


@router.get("/users/{username}", tags=["users"])
async def get_user(username: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == username.lower()))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "username": user.username,
        "display_name": user.display_name,
        "bio": user.bio,
        "avatar_color": user.avatar_color,
        "status": user.status.value if user.status else "offline",
        "status_message": user.status_message,
        "total_messages": user.total_messages,
        "is_online": manager.is_online(user.id),
        "last_seen": user.last_seen.isoformat() if user.last_seen else None,
        "created_at": user.created_at.isoformat(),
    }


# ─── ROOMS ────────────────────────────────────────────────────────

@router.post("/rooms", status_code=201, tags=["rooms"])
async def create_room(req: CreateRoomReq, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    name = req.name.lower().replace(" ", "-")
    result = await db.execute(select(Room).where(Room.name == name))
    if result.scalar_one_or_none():
        raise HTTPException(400, f"Room '{name}' already exists")

    room = Room(
        name=name,
        display_name=req.display_name or name,
        description=req.description,
        topic=req.topic,
        icon=req.icon or "💬",
        room_type=RoomType.PRIVATE if req.is_private else RoomType.PUBLIC,
        owner_id=current_user.id,
        tags=req.tags,
    )
    db.add(room)
    await db.flush()
    db.add(RoomMember(room_id=room.id, user_id=current_user.id, is_admin=True))
    await db.commit()
    manager.subscribe(current_user.id, name)
    return {"message": f"Room '{name}' created", "name": name, "icon": room.icon}


@router.post("/rooms/{room_name}/join", tags=["rooms"])
async def join_room(room_name: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Room).where(Room.name == room_name.lower()))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(404, f"Room '{room_name}' not found")
    if room.room_type == RoomType.PRIVATE:
        raise HTTPException(403, "This is a private room — you need an invite")

    result = await db.execute(select(RoomMember).where(and_(RoomMember.room_id == room.id, RoomMember.user_id == current_user.id)))
    if result.scalar_one_or_none():
        raise HTTPException(400, "Already a member")

    db.add(RoomMember(room_id=room.id, user_id=current_user.id))
    await db.commit()
    manager.subscribe(current_user.id, room.name)

    await manager.broadcast(room.name, {
        "type": "system",
        "room": room.name,
        "content": f"➕ {current_user.display_name or current_user.username} joined #{room.name}",
        "timestamp": datetime.utcnow().isoformat(),
    })
    return {"message": f"Joined #{room_name}"}


@router.post("/rooms/{room_name}/leave", tags=["rooms"])
async def leave_room(room_name: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RoomMember).join(Room).where(and_(Room.name == room_name.lower(), RoomMember.user_id == current_user.id))
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(400, "Not a member")
    await db.delete(member)
    await db.commit()
    manager.unsubscribe(current_user.id, room_name.lower())
    await manager.broadcast(room_name.lower(), {
        "type": "system",
        "room": room_name.lower(),
        "content": f"➖ {current_user.display_name or current_user.username} left #{room_name.lower()}",
        "timestamp": datetime.utcnow().isoformat(),
    })
    return {"message": f"Left #{room_name}"}


@router.get("/rooms", tags=["rooms"])
async def list_rooms(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Room).where(Room.room_type == RoomType.PUBLIC))
    rooms = result.scalars().all()
    return {"rooms": [
        {
            "name": r.name,
            "display_name": r.display_name or r.name,
            "description": r.description,
            "topic": r.topic,
            "icon": r.icon,
            "online": manager.online_in_room(r.name),
            "total_messages": r.total_messages,
            "tags": r.tags,
        }
        for r in rooms
    ]}


@router.get("/rooms/my", tags=["rooms"])
async def my_rooms(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Room).join(RoomMember).where(
            and_(RoomMember.user_id == current_user.id, Room.room_type != RoomType.DIRECT)
        )
    )
    rooms = result.scalars().all()
    return {"rooms": [{"name": r.name, "display_name": r.display_name or r.name, "icon": r.icon, "online": manager.online_in_room(r.name)} for r in rooms]}


@router.get("/rooms/{room_name}/members", tags=["rooms"])
async def room_members(room_name: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User, RoomMember).join(RoomMember, User.id == RoomMember.user_id).join(Room).where(Room.name == room_name.lower())
    )
    rows = result.all()
    return {"members": [
        {
            "username": u.username,
            "display_name": u.display_name,
            "avatar_color": u.avatar_color,
            "is_admin": m.is_admin,
            "is_online": manager.is_online(u.id),
            "nickname": m.nickname,
        }
        for u, m in rows
    ]}


# ─── MESSAGES ─────────────────────────────────────────────────────

@router.get("/messages/history/{room_name}", tags=["messages"])
async def get_history(
    room_name: str,
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Room).join(RoomMember).where(and_(Room.name == room_name.lower(), RoomMember.user_id == current_user.id))
    )
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(403, "Not a member of this room")

    result = await db.execute(
        select(Message, User.username, User.display_name, User.avatar_color)
        .join(User, Message.sender_id == User.id)
        .where(and_(Message.room_id == room.id, Message.is_deleted == False))
        .order_by(desc(Message.created_at))
        .limit(limit).offset(offset)
    )
    rows = result.all()

    messages = []
    for msg, uname, dname, color in reversed(rows):
        m = {
            "id": msg.id,
            "sender": uname,
            "display_name": dname,
            "avatar_color": color,
            "content": msg.content,
            "message_type": msg.message_type.value,
            "is_pinned": msg.is_pinned,
            "timestamp": msg.created_at.isoformat(),
            "edited_at": msg.edited_at.isoformat() if msg.edited_at else None,
        }
        # Reactions
        r_result = await db.execute(select(Reaction).where(Reaction.message_id == msg.id))
        reactions_raw = r_result.scalars().all()
        emoji_counts = {}
        for r in reactions_raw:
            emoji_counts[r.emoji] = emoji_counts.get(r.emoji, 0) + 1
        m["reactions"] = emoji_counts

        # Reply context
        if msg.reply_to_id:
            rr = await db.execute(select(Message, User.username).join(User, Message.sender_id == User.id).where(Message.id == msg.reply_to_id))
            row = rr.first()
            if row:
                rm, runame = row
                m["reply_to"] = {"id": rm.id, "sender": runame, "content": (rm.content or "")[:100]}

        if msg.file_id:
            fr = await db.execute(select(FileUpload).where(FileUpload.id == msg.file_id))
            f = fr.scalar_one_or_none()
            if f:
                m["file"] = {"id": f.id, "filename": f.original_filename, "size": f.file_size, "mime_type": f.mime_type}
        messages.append(m)

    return {"room": room_name, "messages": messages}


@router.delete("/messages/{message_id}", tags=["messages"])
async def delete_message(message_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg.sender_id != current_user.id and not current_user.is_admin:
        raise HTTPException(403, "Cannot delete someone else's message")
    msg.is_deleted = True
    msg.content = "[deleted]"
    await db.commit()

    room_result = await db.execute(select(Room).where(Room.id == msg.room_id))
    room = room_result.scalar_one_or_none()
    if room:
        await manager.broadcast(room.name, {"type": "message_deleted", "message_id": message_id, "room": room.name})
    return {"message": "Message deleted"}


@router.patch("/messages/{message_id}", tags=["messages"])
async def edit_message(
    message_id: int,
    content: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg or msg.sender_id != current_user.id:
        raise HTTPException(403, "Cannot edit this message")
    msg.content = content
    msg.edited_at = datetime.utcnow()
    await db.commit()

    room_result = await db.execute(select(Room).where(Room.id == msg.room_id))
    room = room_result.scalar_one_or_none()
    if room:
        await manager.broadcast(room.name, {
            "type": "message_edited",
            "message_id": message_id,
            "content": content,
            "room": room.name,
            "edited_at": msg.edited_at.isoformat(),
        })
    return {"message": "Edited"}


# ─── FILES ────────────────────────────────────────────────────────

@router.post("/files/upload", tags=["files"])
async def upload_file(
    file: UploadFile = File(...),
    room: Optional[str] = None,
    recipient: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ext = Path(file.filename).suffix.lower().lstrip(".")
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type '.{ext}' not allowed")

    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(413, f"File too large (max {settings.MAX_FILE_SIZE // 1024 // 1024}MB)")

    safe_name = f"{uuid.uuid4().hex}.{ext}"
    file_path = Path(settings.UPLOAD_DIR) / safe_name
    file_path.write_bytes(content)

    mime = mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    db_file = FileUpload(
        filename=safe_name,
        original_filename=file.filename,
        file_size=len(content),
        mime_type=mime,
        storage_path=str(file_path),
        uploader_id=current_user.id,
    )
    db.add(db_file)
    await db.flush()

    target_room_name = None
    if room:
        result = await db.execute(select(Room).join(RoomMember).where(and_(Room.name == room.lower(), RoomMember.user_id == current_user.id)))
        target_room = result.scalar_one_or_none()
        if target_room:
            target_room_name = target_room.name
            is_image = mime.startswith("image/")
            msg_type = MessageType.IMAGE if is_image else MessageType.FILE
            db.add(Message(room_id=target_room.id, sender_id=current_user.id, content=f"{'🖼' if is_image else '📎'} {file.filename} ({_fmt_size(len(content))})", message_type=msg_type, file_id=db_file.id))
    elif recipient:
        result = await db.execute(select(User).where(User.username == recipient.lower()))
        other = result.scalar_one_or_none()
        if other:
            dm_name = "dm_" + "_".join(sorted([current_user.username, other.username]))
            result2 = await db.execute(select(Room).where(Room.name == dm_name))
            dm = result2.scalar_one_or_none()
            if not dm:
                dm = Room(name=dm_name, room_type=RoomType.DIRECT)
                db.add(dm)
                await db.flush()
                db.add(RoomMember(room_id=dm.id, user_id=current_user.id))
                db.add(RoomMember(room_id=dm.id, user_id=other.id))
            target_room_name = dm_name
            is_image = mime.startswith("image/")
            db.add(Message(room_id=dm.id, sender_id=current_user.id, content=f"{'🖼' if is_image else '📎'} {file.filename} ({_fmt_size(len(content))})", message_type=MessageType.IMAGE if is_image else MessageType.FILE, file_id=db_file.id))

    await db.commit()
    await db.refresh(db_file)

    if target_room_name:
        await manager.broadcast(target_room_name, {
            "type": "message",
            "room": target_room_name,
            "sender": current_user.username,
            "content": f"📎 {file.filename}",
            "message_type": "file",
            "file": {"id": db_file.id, "filename": file.filename, "size": len(content), "mime_type": mime},
            "timestamp": datetime.utcnow().isoformat(),
        })

    return {"file_id": db_file.id, "filename": file.filename, "size": len(content), "mime_type": mime, "download_url": f"/api/v1/files/{db_file.id}/download"}


@router.get("/files/{file_id}/download", tags=["files"])
async def download_file(file_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FileUpload).where(FileUpload.id == file_id))
    f = result.scalar_one_or_none()
    if not f or not os.path.exists(f.storage_path):
        raise HTTPException(404, "File not found")

    f.download_count = (f.download_count or 0) + 1
    await db.commit()

    def stream():
        with open(f.storage_path, "rb") as fp:
            while chunk := fp.read(65536):
                yield chunk

    return StreamingResponse(stream(), media_type=f.mime_type or "application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{f.original_filename}"',
        "Content-Length": str(f.file_size),
    })


# ─── FRIENDS ──────────────────────────────────────────────────────

@router.post("/friends/add/{username}", tags=["friends"])
async def add_friend(username: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if username.lower() == current_user.username:
        raise HTTPException(400, "Cannot add yourself")

    result = await db.execute(select(User).where(User.username == username.lower()))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    result = await db.execute(select(Friendship).where(
        or_(
            and_(Friendship.requester_id == current_user.id, Friendship.addressee_id == target.id),
            and_(Friendship.requester_id == target.id, Friendship.addressee_id == current_user.id),
        )
    ))
    existing = result.scalar_one_or_none()
    if existing:
        if existing.status == FriendStatus.ACCEPTED:
            raise HTTPException(400, "Already friends")
        elif existing.status == FriendStatus.PENDING:
            raise HTTPException(400, "Request already pending")

    db.add(Friendship(requester_id=current_user.id, addressee_id=target.id, status=FriendStatus.PENDING))
    await db.commit()

    await manager.send(target.id, {
        "type": "friend_request",
        "from": current_user.username,
        "display_name": current_user.display_name,
        "avatar_color": current_user.avatar_color,
        "timestamp": datetime.utcnow().isoformat(),
    })
    return {"message": f"Friend request sent to {username}"}


@router.post("/friends/accept/{username}", tags=["friends"])
async def accept_friend(username: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == username.lower()))
    requester = result.scalar_one_or_none()
    if not requester:
        raise HTTPException(404, "User not found")

    result = await db.execute(select(Friendship).where(
        and_(Friendship.requester_id == requester.id, Friendship.addressee_id == current_user.id, Friendship.status == FriendStatus.PENDING)
    ))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(404, "No pending request")

    f.status = FriendStatus.ACCEPTED
    f.updated_at = datetime.utcnow()

    # Create DM room
    dm_name = "dm_" + "_".join(sorted([current_user.username, requester.username]))
    result2 = await db.execute(select(Room).where(Room.name == dm_name))
    if not result2.scalar_one_or_none():
        dm = Room(name=dm_name, room_type=RoomType.DIRECT)
        db.add(dm)
        await db.flush()
        db.add(RoomMember(room_id=dm.id, user_id=current_user.id))
        db.add(RoomMember(room_id=dm.id, user_id=requester.id))

    await db.commit()
    await manager.send(requester.id, {"type": "friend_accepted", "by": current_user.username, "avatar_color": current_user.avatar_color, "timestamp": datetime.utcnow().isoformat()})
    return {"message": f"Now friends with {username}!"}


@router.post("/friends/reject/{username}", tags=["friends"])
async def reject_friend(username: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == username.lower()))
    requester = result.scalar_one_or_none()
    if not requester:
        raise HTTPException(404, "User not found")
    result = await db.execute(select(Friendship).where(and_(Friendship.requester_id == requester.id, Friendship.addressee_id == current_user.id, Friendship.status == FriendStatus.PENDING)))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(404, "No pending request")
    f.status = FriendStatus.REJECTED
    await db.commit()
    return {"message": "Request rejected"}


@router.get("/friends/list", tags=["friends"])
async def list_friends(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Friendship).where(
        and_(or_(Friendship.requester_id == current_user.id, Friendship.addressee_id == current_user.id), Friendship.status == FriendStatus.ACCEPTED)
    ))
    friends = []
    for f in result.scalars().all():
        fid = f.addressee_id if f.requester_id == current_user.id else f.requester_id
        u = await db.execute(select(User).where(User.id == fid))
        user = u.scalar_one_or_none()
        if user:
            friends.append({
                "username": user.username,
                "display_name": user.display_name,
                "avatar_color": user.avatar_color,
                "status": user.status.value if user.status else "offline",
                "status_message": user.status_message,
                "is_online": manager.is_online(user.id),
                "last_seen": user.last_seen.isoformat() if user.last_seen else None,
            })
    return {"friends": friends}


@router.get("/friends/requests", tags=["friends"])
async def friend_requests(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Friendship).where(and_(Friendship.addressee_id == current_user.id, Friendship.status == FriendStatus.PENDING)))
    requests = []
    for f in result.scalars().all():
        u = await db.execute(select(User).where(User.id == f.requester_id))
        user = u.scalar_one_or_none()
        if user:
            requests.append({"username": user.username, "display_name": user.display_name, "avatar_color": user.avatar_color, "sent_at": f.created_at.isoformat()})
    return {"requests": requests}


# ─── POLLS ────────────────────────────────────────────────────────

@router.post("/polls", tags=["polls"])
async def create_poll(req: CreatePollReq, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if len(req.options) < 2 or len(req.options) > 10:
        raise HTTPException(400, "Polls need 2-10 options")

    result = await db.execute(select(Room).join(RoomMember).where(and_(Room.name == req.room.lower(), RoomMember.user_id == current_user.id)))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(403, "Not a member of this room")

    poll_content = f"📊 **{req.question}**\n" + "\n".join(f"  {i+1}. {opt}" for i, opt in enumerate(req.options))
    msg = Message(room_id=room.id, sender_id=current_user.id, content=poll_content, message_type=MessageType.POLL)
    db.add(msg)
    await db.flush()

    poll = Poll(message_id=msg.id, question=req.question, options=req.options, is_multiple=req.is_multiple, is_anonymous=req.is_anonymous)
    db.add(poll)
    await db.commit()

    await manager.broadcast(room.name, {
        "type": "poll",
        "message_id": msg.id,
        "poll_id": poll.id,
        "room": room.name,
        "sender": current_user.username,
        "question": req.question,
        "options": req.options,
        "is_multiple": req.is_multiple,
        "is_anonymous": req.is_anonymous,
        "vote_counts": {},
        "timestamp": datetime.utcnow().isoformat(),
    })
    return {"poll_id": poll.id, "message_id": msg.id}


# ─── STATS ────────────────────────────────────────────────────────

@router.get("/stats", tags=["stats"])
async def server_stats(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user_count = await db.scalar(select(func.count(User.id)).where(User.is_active == True))
    room_count = await db.scalar(select(func.count(Room.id)))
    msg_count = await db.scalar(select(func.count(Message.id)).where(Message.is_deleted == False))
    file_count = await db.scalar(select(func.count(FileUpload.id)))

    return {
        "users": user_count,
        "rooms": room_count,
        "messages": msg_count,
        "files": file_count,
        "online_now": manager.online_count(),
    }


def _fmt_size(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size //= 1024
    return f"{size:.1f}GB"
