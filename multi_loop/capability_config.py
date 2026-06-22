"""Persistent, approval-gated command capability configuration."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .capabilities import CapabilityRegistry, default_capabilities
from .models import Capability, SideEffectClass, from_dict, to_dict, utc_now_iso
from .policy import resolve_within


@dataclass(slots=True)
class ConfiguredCapability:
    capability: Capability
    configured_by: str
    approval_evidence: str
    created_at: str = field(default_factory=utc_now_iso)


class ConfiguredCapabilityStore:
    """Store user-approved tool cards under the multi-loop state root."""

    def __init__(self, root: str | Path = ".multi-loop") -> None:
        self.root = Path(root)
        self.directory = self.root / "main-loop" / "configured-capabilities"

    def save(self, configured: ConfiguredCapability) -> Path:
        name = configured.capability.name
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name) is None:
            raise ValueError("Capability name must be lowercase snake_case.")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = resolve_within(self.directory, f"{name}.json")
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(to_dict(configured), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        return path

    def add_command(
        self,
        *,
        name: str,
        description: str,
        command: str,
        side_effect_class: str | SideEffectClass,
        configured_by: str,
        approval_evidence: str,
        runner: str = "agent_command",
        verification: str = "Require durable artifacts and verifiable external handles.",
    ) -> ConfiguredCapability:
        if runner not in {"agent_command", "shell"}:
            raise ValueError("Configured command runner must be agent_command or shell.")
        clean_command = command.strip()
        if not clean_command or not shlex.split(clean_command):
            raise ValueError("Configured capability command cannot be empty.")
        lowered = clean_command.lower()
        if any(marker in lowered for marker in ("api_key=", "token=", "authorization:")):
            raise ValueError("Do not embed credentials in capability commands; use environment references.")
        if not configured_by.strip() or not approval_evidence.strip():
            raise ValueError("User approval evidence is required to add a command capability.")
        capability = Capability(
            name=name.strip(),
            description=description.strip(),
            toolset_or_backend="configured_command",
            side_effect_class=SideEffectClass(side_effect_class),
            inputs=["candidate prompt", "workspace"],
            outputs=["summary", "artifacts", "execution transcript"],
            artifact_types=["markdown", "terminal transcript", "files"],
            availability_check=f"requires executable: {shlex.split(clean_command)[0]}",
            verification=verification.strip(),
            tags=["configured", "command", runner],
            runner=runner,
            runner_command=clean_command,
            setup_hint=f"Install and authenticate {shlex.split(clean_command)[0]}.",
        )
        configured = ConfiguredCapability(
            capability=capability,
            configured_by=configured_by.strip(),
            approval_evidence=approval_evidence.strip(),
        )
        self.save(configured)
        return configured

    def list(self) -> list[ConfiguredCapability]:
        if not self.directory.exists():
            return []
        configured: list[ConfiguredCapability] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    configured.append(from_dict(ConfiguredCapability, json.load(handle)))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return configured


def configured_capabilities(root: str | Path = ".multi-loop") -> CapabilityRegistry:
    """Merge built-ins with user-approved configured command tools."""
    registry = default_capabilities()
    for configured in ConfiguredCapabilityStore(root).list():
        capability = configured.capability
        registry.register(
            capability,
            check=lambda command=capability.runner_command: _command_available(command),
            override=True,
        )
    return registry


def _command_available(command: str | None) -> bool:
    if not command:
        return False
    try:
        executable = shlex.split(command)[0]
    except (ValueError, IndexError):
        return False
    if "/" in executable:
        return Path(executable).is_file() and os.access(executable, os.X_OK)
    return shutil.which(executable) is not None
