"""Native provider profiles and OpenAI-compatible chat transport.

Profiles contain configuration and an environment-variable reference only.
Credential values are never persisted in multi-loop storage.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .models import from_dict, to_dict, utc_now_iso
from .policy import resolve_within


PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai_compatible": ("http://127.0.0.1:11434/v1", ""),
}


@dataclass(slots=True)
class ProviderProfile:
    id: str
    kind: str
    model: str
    base_url: str
    api_key_env: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class ProviderToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderReply:
    content: str = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_finish_reason: str | None = None


class ProviderClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderReply: ...


class ProviderStore:
    def __init__(self, root: str | Path = ".multi-loop") -> None:
        self.root = Path(root)
        self.directory = self.root / "main-loop" / "providers"

    def save(self, profile: ProviderProfile) -> Path:
        if not profile.id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in profile.id):
            raise ValueError("Provider id may contain only letters, numbers, '-' and '_'.")
        if profile.kind not in PROVIDER_DEFAULTS:
            raise ValueError(f"Unsupported provider kind: {profile.kind}")
        if not profile.model.strip():
            raise ValueError("Provider model is required.")
        if not profile.base_url.startswith(("http://", "https://")):
            raise ValueError("Provider base URL must use http:// or https://.")
        # Block accidental secret persistence in the reference field.
        if profile.api_key_env and re.fullmatch(r"[A-Z_][A-Z0-9_]*", profile.api_key_env) is None:
            raise ValueError("api_key_env must be an environment-variable name, not a credential.")
        allowed_headers = {"http-referer", "x-title", "user-agent"}
        unsupported_headers = [
            key for key in profile.headers if key.lower() not in allowed_headers
        ]
        if unsupported_headers:
            raise ValueError(
                "Only non-secret provider metadata headers may be persisted; use api_key_env "
                "for credentials."
            )
        profile.updated_at = utc_now_iso()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = resolve_within(self.directory, f"{profile.id}.json")
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(to_dict(profile), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        return path

    def connect(
        self,
        provider_id: str,
        *,
        kind: str,
        model: str,
        base_url: str | None = None,
        api_key_env: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> ProviderProfile:
        try:
            default_url, default_env = PROVIDER_DEFAULTS[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported provider kind: {kind}") from exc
        profile = ProviderProfile(
            id=provider_id,
            kind=kind,
            model=model.strip(),
            base_url=(base_url or default_url).rstrip("/"),
            api_key_env=default_env if api_key_env is None else api_key_env,
            headers=headers or {},
        )
        self.save(profile)
        return profile

    def load(self, provider_id: str) -> ProviderProfile:
        path = resolve_within(self.directory, f"{provider_id}.json")
        try:
            with path.open("r", encoding="utf-8") as handle:
                return from_dict(ProviderProfile, json.load(handle))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Provider profile not found: {provider_id}") from exc

    def list(self) -> list[ProviderProfile]:
        if not self.directory.exists():
            return []
        profiles: list[ProviderProfile] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    profiles.append(from_dict(ProviderProfile, json.load(handle)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return profiles

    def remove(self, provider_id: str) -> None:
        path = resolve_within(self.directory, f"{provider_id}.json")
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Provider profile not found: {provider_id}") from exc


class OpenAICompatibleClient:
    """Small stdlib transport for providers implementing chat completions."""

    def __init__(self, profile: ProviderProfile, *, timeout: float = 120.0) -> None:
        self.profile = profile
        self.timeout = timeout

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ProviderReply:
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = self._request("POST", "/chat/completions", payload)
        try:
            choice = response["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Provider returned an invalid chat-completions response.") from exc
        calls: list[ProviderToolCall] = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Provider emitted invalid tool arguments for {function.get('name')}.") from exc
            calls.append(
                ProviderToolCall(
                    id=str(item.get("id") or f"call-{len(calls)}"),
                    name=str(function.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        usage = response.get("usage") or {}
        return ProviderReply(
            content=str(message.get("content") or ""),
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            raw_finish_reason=choice.get("finish_reason"),
        )

    def validate(self) -> dict[str, Any]:
        response = self._request("GET", "/models")
        models = response.get("data") if isinstance(response, dict) else None
        ids = [str(item.get("id")) for item in models or [] if isinstance(item, dict)]
        return {
            "ok": True,
            "provider_id": self.profile.id,
            "configured_model": self.profile.model,
            "model_visible": self.profile.model in ids if ids else None,
            "model_count": len(ids),
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json", **self.profile.headers}
        credential = os.environ.get(self.profile.api_key_env) if self.profile.api_key_env else None
        if self.profile.api_key_env and not credential:
            raise RuntimeError(
                f"Provider {self.profile.id} requires environment variable {self.profile.api_key_env}."
            )
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.profile.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Provider HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Provider connection failed: {exc.reason}") from exc
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Provider returned non-JSON content.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Provider returned an invalid JSON response.")
        return value
