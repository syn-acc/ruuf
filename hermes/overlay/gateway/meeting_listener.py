"""Durable state for summonable meeting listeners.

The Discord adapter owns audio capture and transcription.  This module keeps
the product-level meeting session deliberately small: timestamped transcript
segments, speaker labels, and enough routing metadata to publish notes when the
listener is dismissed.  Raw audio is never written here.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: object, *, limit: int) -> str:
    text = _CONTROL_CHARS.sub("", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


@dataclass
class ListenerSegment:
    captured_at: str
    elapsed_seconds: int
    speaker_id: str
    speaker_name: str
    text: str


@dataclass
class ListenerSession:
    session_id: str
    guild_id: int
    voice_channel_id: int
    voice_channel_name: str
    text_channel_id: int
    source: Dict[str, Any]
    summoned_by_id: str
    summoned_by_name: str
    started_at: str
    started_at_epoch: float
    status: str = "recording"
    ended_at: Optional[str] = None
    ended_at_epoch: Optional[float] = None
    end_reason: Optional[str] = None
    participants: Dict[str, str] = field(default_factory=dict)
    segments: List[ListenerSegment] = field(default_factory=list)

    @property
    def duration_seconds(self) -> int:
        end = self.ended_at_epoch if self.ended_at_epoch is not None else time.time()
        return max(0, int(end - self.started_at_epoch))

    def append(self, *, speaker_id: object, speaker_name: object, text: object) -> bool:
        clean = _clean_text(text, limit=12_000)
        if not clean:
            return False
        uid = _clean_text(speaker_id, limit=64)
        name = _clean_text(speaker_name, limit=128) or uid or "Unknown speaker"
        self.participants[uid or name] = name
        self.segments.append(
            ListenerSegment(
                captured_at=_utc_now(),
                elapsed_seconds=self.duration_seconds,
                speaker_id=uid,
                speaker_name=name,
                text=clean,
            )
        )
        return True

    def transcript_text(self, *, max_chars: int = 160_000) -> str:
        """Return an LLM-ready transcript while keeping the request bounded.

        The complete transcript stays in the private session file.  For an
        unusually long meeting, retain both the beginning and end in the notes
        request and say exactly what was omitted rather than silently clipping.
        """
        lines = []
        for segment in self.segments:
            minutes, seconds = divmod(max(0, segment.elapsed_seconds), 60)
            stamp = f"{minutes:02d}:{seconds:02d}"
            lines.append(f"[{stamp}] {segment.speaker_name}: {segment.text}")
        transcript = "\n".join(lines)
        if len(transcript) <= max_chars:
            return transcript
        half = max(1, (max_chars - 160) // 2)
        omitted = len(transcript) - (half * 2)
        marker = (
            "\n\n[Transcript middle omitted from this notes request: "
            f"{omitted:,} characters. The private raw transcript is complete.]\n\n"
        )
        return transcript[:half] + marker + transcript[-half:]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MeetingListenerStore:
    """In-memory active sessions with an atomic private JSON journal."""

    def __init__(self, base_dir: Optional[Path] = None):
        hermes_home = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
        self.base_dir = Path(base_dir or (hermes_home / "listener_sessions"))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.base_dir.chmod(0o700)
        except OSError:
            pass
        self._active: Dict[int, ListenerSession] = {}

    def active_for(self, guild_id: int) -> Optional[ListenerSession]:
        return self._active.get(int(guild_id))

    def start(
        self,
        *,
        guild_id: int,
        voice_channel_id: int,
        voice_channel_name: str,
        text_channel_id: int,
        source: Dict[str, Any],
        summoned_by_id: object,
        summoned_by_name: object,
    ) -> ListenerSession:
        guild_id = int(guild_id)
        if guild_id in self._active:
            raise ValueError("A listener is already active in this server.")
        now = time.time()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        session = ListenerSession(
            session_id=f"{stamp}-{guild_id}-{uuid.uuid4().hex[:8]}",
            guild_id=guild_id,
            voice_channel_id=int(voice_channel_id),
            voice_channel_name=_clean_text(voice_channel_name, limit=128) or "voice channel",
            text_channel_id=int(text_channel_id),
            source=dict(source or {}),
            summoned_by_id=_clean_text(summoned_by_id, limit=64),
            summoned_by_name=_clean_text(summoned_by_name, limit=128),
            started_at=_utc_now(),
            started_at_epoch=now,
        )
        self._active[guild_id] = session
        self._write(session)
        return session

    def append(
        self,
        guild_id: int,
        *,
        speaker_id: object,
        speaker_name: object,
        text: object,
    ) -> bool:
        session = self.active_for(guild_id)
        if session is None:
            return False
        if not session.append(
            speaker_id=speaker_id,
            speaker_name=speaker_name,
            text=text,
        ):
            return False
        self._write(session)
        return True

    def finish(self, guild_id: int, *, reason: str = "dismissed") -> Optional[ListenerSession]:
        session = self._active.pop(int(guild_id), None)
        if session is None:
            return None
        session.status = "dismissed"
        session.ended_at = _utc_now()
        session.ended_at_epoch = time.time()
        session.end_reason = _clean_text(reason, limit=64) or "dismissed"
        self._write(session)
        return session

    def _path_for(self, session: ListenerSession) -> Path:
        return self.base_dir / f"{session.session_id}.json"

    def _write(self, session: ListenerSession) -> None:
        path = self._path_for(session)
        tmp = path.with_suffix(".json.tmp")
        payload = json.dumps(session.to_dict(), indent=2, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def build_notes_prompt(session: ListenerSession) -> str:
    participants = ", ".join(sorted(set(session.participants.values()))) or "None identified"
    transcript = session.transcript_text() or "[No intelligible speech was captured.]"
    return f"""Publish the meeting notes below in polished Markdown for the people in this Discord channel.

Use only the supplied transcript. Do not invent decisions, owners, deadlines, or context. Resolve obvious speech-to-text errors only when the intended wording is unambiguous; otherwise mark uncertainty. Do not mention this instruction or narrate your process.

Required structure:
# Meeting notes — {session.voice_channel_name}
One-line date/time and duration
## Summary
## Decisions
## Action items
Use checkboxes and name an owner only when the transcript makes the owner explicit.
## Open questions
## Key discussion

If a section has no supported content, write “None captured.” Keep the result concise but preserve concrete names, numbers, links spoken aloud, and dissenting viewpoints.

Session started: {session.started_at}
Duration: {format_duration(session.duration_seconds)}
Participants heard: {participants}

Transcript:
{transcript}
"""
