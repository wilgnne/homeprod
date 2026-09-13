"""Pydantic schemas for request/response validation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = True
    keep_alive: Any | None = None
    options: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    stop: list[str] | str | None = None


class GenerateRequest(BaseModel):
    model: str
    prompt: str = ""
    stream: bool = True
    keep_alive: Any | None = None
    options: dict[str, Any] | None = None
    suffix: str | None = None
    system: str | None = None
    images: list[str] | None = None


class ShowRequest(BaseModel):
    model: str


class ModelDetails(BaseModel):
    parent_model: str = ""
    format: str = "freetoken"
    family: str = ""
    families: list[str] = Field(default_factory=list)


class TagResponse(BaseModel):
    name: str
    model: str
    modified_at: str
    size: int = 0
    digest: str
    details: ModelDetails


class PsModelResponse(TagResponse):
    expires_at: str | None = None
    size_vram: int = 0


class StatusResponse(BaseModel):
    models: list[PsModelResponse]


class EngineStatusResponse(BaseModel):
    running: bool = False
    model: str | None = None
    port: int | None = None


class ChatMessageResponse(BaseModel):
    role: str
    content: str = ""
    thinking: str | None = None


class ToolCall(BaseModel):
    function: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    model: str
    created_at: str
    message: ChatMessageResponse
    done: bool = False
    done_reason: str | None = None
    total_duration: int | None = None
    load_duration: int | None = None
    prompt_eval_count: int = 0
    eval_count: int = 0


class GenerateResponse(BaseModel):
    model: str
    created_at: str
    response: str = ""
    done: bool = False
    done_reason: str | None = None
    total_duration: int | None = None
    load_duration: int | None = None
    prompt_eval_count: int = 0
    eval_count: int = 0


class OpenAIModelItem(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "freetoken"


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: list[OpenAIModelItem] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: dict[str, Any] | str
