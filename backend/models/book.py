"""书籍相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class BookResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    book_id: str
    book_name: Optional[str] = None
    author: Optional[str] = None
    category: Optional[str] = None
    total_chapters: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    status: str = ""
    book_status: Optional[str] = "pending"
    note: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BookCreate(BaseModel):
    book_id: str
    book_name: str
    author: Optional[str] = None
    category: Optional[str] = None
    total_chapters: Optional[int] = None
    book_data: Optional[dict] = None
    tags: list[str] = Field(default_factory=list)
    note: Optional[str] = None


class BookUpdate(BaseModel):
    book_name: Optional[str] = None
    author: Optional[str] = None
    category: Optional[str] = None
    total_chapters: Optional[int] = None
    book_data: Optional[dict] = None
    tags: Optional[list[str]] = None
    note: Optional[str] = None


class BookTagsUpdate(BaseModel):
    tags: list[str]
