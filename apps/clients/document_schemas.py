from datetime import datetime
from typing import Annotated
from ninja import Schema
from pydantic import StringConstraints
from .models import ClientDocument

FolderName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
DocumentTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class FolderInput(Schema):
    name: FolderName


class FolderOutput(Schema):
    id: int
    name: str


class DocumentUpdate(Schema):
    title: DocumentTitle
    category: ClientDocument.Category
    folder_id: int | None = None


class DocumentOutput(Schema):
    id: int
    client_id: int
    title: str
    category: str
    folder_id: int | None
    folder_name: str | None
    original_name: str
    content_type: str
    size_bytes: int
    uploaded_by: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class DocumentPage(Schema):
    items: list[DocumentOutput]
    total: int
