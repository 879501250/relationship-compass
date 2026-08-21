"""Shared direct-import test helpers."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import memory_store  # noqa: E402


class DirectMemoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(
            os.environ,
            {"GOUTOUJUNSHI_PERSONAL_MEMORY_DIR": self.temp_dir.name},
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.temp_dir.cleanup()

    def call(self, function: Callable[[argparse.Namespace], None], **kwargs: Any) -> dict[str, Any]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            function(argparse.Namespace(**kwargs))
        return json.loads(output.getvalue())

    def enable(self) -> None:
        self.call(memory_store.command_enable, confirm=True)

    def apply(self, delta: dict[str, Any]) -> dict[str, Any]:
        return self.call(
            memory_store.command_apply,
            json=json.dumps(delta, ensure_ascii=False),
            file=None,
        )

    def context(self, subject_id: str, max_chars: int = 4000) -> dict[str, Any]:
        return self.call(
            memory_store.command_context,
            subject_id=subject_id,
            max_chars=max_chars,
        )

    def show(self, subject_id: str | None = None) -> dict[str, Any]:
        return self.call(memory_store.command_show, subject_id=subject_id)

    @staticmethod
    def user_delta(field: str, value: str, source_type: str = "user_explicit") -> dict[str, Any]:
        return {
            "scope": "user",
            "subject_id": "user",
            "field": field,
            "value": value,
            "source_type": source_type,
            "source_ref": "test:user",
            "confidence": "high",
        }

    @staticmethod
    def object_delta(subject_id: str, field: str, value: str) -> dict[str, Any]:
        return {
            "scope": "object",
            "subject_id": subject_id,
            "field": field,
            "value": value,
            "source_type": "user_report",
            "source_ref": "test:object",
            "confidence": "high",
        }

    @staticmethod
    def event_delta(
        subject_id: str,
        field: str,
        value: str,
        occurred_at: str,
        retention: str,
    ) -> dict[str, Any]:
        return {
            "scope": "event",
            "subject_id": subject_id,
            "field": field,
            "value": value,
            "source_type": "user_report",
            "source_ref": "test:event",
            "occurred_at": occurred_at,
            "retention": retention,
            "confidence": "high",
        }

    @staticmethod
    def hypothesis_delta(subject_id: str, field: str, value: str) -> dict[str, Any]:
        return {
            "scope": "hypothesis",
            "subject_id": subject_id,
            "field": field,
            "value": value,
            "source_type": "assistant_inference",
            "source_ref": "test:hypothesis",
            "confidence": "medium",
        }

# Modified by AI on 2026-08-21 14:47:55
