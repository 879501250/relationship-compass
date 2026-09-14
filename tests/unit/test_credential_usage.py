from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from eval_console import service


class CredentialUsageTests(unittest.TestCase):
    def test_only_successful_target_and_judge_outcomes_mark_credentials_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._write_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "INVALID_STRUCTURED_OUTPUT"}],
            )
            store = mock.Mock()
            request = SimpleNamespace(registry_root=Path(raw) / "registry")
            with mock.patch.object(service, "RegistryStore", return_value=store):
                service._mark_used_credentials(
                    run_dir,
                    request,
                    self._provider("target-credential"),
                    self._provider("judge-credential"),
                )
            self.assertEqual(
                store.mark_credential_used.call_args_list,
                [
                    mock.call("target-credential", mock.ANY),
                    mock.call("judge-credential", mock.ANY),
                ],
            )

    def test_failed_http_outcomes_do_not_mark_credentials_used(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(
                run_dir / "responses.jsonl",
                [{"status": "TARGET_ERROR", "error_code": "UNAUTHORIZED"}],
            )
            self._write_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "RATE_LIMIT"}],
            )
            store = mock.Mock()
            request = SimpleNamespace(registry_root=Path(raw) / "registry")
            with mock.patch.object(service, "RegistryStore", return_value=store):
                service._mark_used_credentials(
                    run_dir,
                    request,
                    self._provider("target-credential"),
                    self._provider("judge-credential"),
                )
            store.mark_credential_used.assert_not_called()

    @staticmethod
    def _provider(credential_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            _registry_runtime_record={"identity_snapshot": {"credential_id": credential_id}}
        )

    @staticmethod
    def _write_records(path: Path, records: list[dict[str, object]]) -> None:
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
