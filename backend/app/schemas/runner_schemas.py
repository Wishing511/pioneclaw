from datetime import datetime

from pydantic import BaseModel


class BindUserRequest(BaseModel):
    user_id: int


class SetDefaultRunnerRequest(BaseModel):
    runner_id: int


class RotateTokenResponse(BaseModel):
    new_token: str
    rotated_at: datetime
    old_token_expires_at: datetime


class DiagnosticsResponse(BaseModel):
    cpu_percent: float = 0
    memory_percent: float = 0
    disk_percent: float = 0
    processes: list = []
    updated_at: datetime | None = None


class LocalLogQuery(BaseModel):
    category: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = 100


class LocalLogEntry(BaseModel):
    timestamp: datetime
    category: str
    level: str
    message: str


class ConnectionEventResponse(BaseModel):
    id: int
    runner_id: int
    event_type: str
    detail: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class RunnerReleaseResponse(BaseModel):
    id: int
    version: str
    filename: str
    file_size: int
    checksum: str
    platform: str
    release_notes: str | None = None
    is_latest: bool
    uploaded_by: int
    created_at: datetime

    class Config:
        from_attributes = True
