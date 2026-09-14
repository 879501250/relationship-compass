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
            scope = service._credential_usage_scope(run_dir)
            self._append_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._append_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "INVALID_STRUCTURED_OUTPUT"}],
            )
            store = mock.Mock()
            request = SimpleNamespace(registry_root=Path(raw) / "registry")
            with mock.patch.object(service, "RegistryStore", return_value=store):
                service._mark_used_credentials(
                    run_dir,
                    scope,
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
            scope = service._credential_usage_scope(run_dir)
            self._append_records(
                run_dir / "responses.jsonl",
                [{"status": "TARGET_ERROR", "error_code": "UNAUTHORIZED"}],
            )
            self._append_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "RATE_LIMIT"}],
            )
            store = mock.Mock()
            request = SimpleNamespace(registry_root=Path(raw) / "registry")
            with mock.patch.object(service, "RegistryStore", return_value=store):
                service._mark_used_credentials(
                    run_dir,
                    scope,
                    request,
                    self._provider("target-credential"),
                    self._provider("judge-credential"),
                )
            store.mark_credential_used.assert_not_called()

    def test_historical_target_success_does_not_mark_resume_401_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            scope = service._credential_usage_scope(run_dir)
            self._append_records(
                run_dir / "responses.jsonl",
                [{"status": "TARGET_ERROR", "error_code": "UNAUTHORIZED"}],
            )

            store = self._mark(run_dir, scope)

            store.mark_credential_used.assert_not_called()

    def test_historical_judge_success_does_not_mark_resume_429_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "judgments.jsonl", [{"status": "JUDGMENT"}])
            scope = service._credential_usage_scope(run_dir)
            self._append_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "RATE_LIMIT"}],
            )

            store = self._mark(run_dir, scope)

            store.mark_credential_used.assert_not_called()

    def test_current_success_marks_even_when_history_already_contains_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._write_records(run_dir / "judgments.jsonl", [{"status": "JUDGMENT"}])
            scope = service._credential_usage_scope(run_dir)
            self._append_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._append_records(run_dir / "judgments.jsonl", [{"status": "JUDGMENT"}])

            store = self._mark(run_dir, scope)

            self.assertEqual(
                store.mark_credential_used.call_args_list,
                [
                    mock.call("target-credential", mock.ANY),
                    mock.call("judge-credential", mock.ANY),
                ],
            )

    def test_interrupted_execution_marks_only_role_with_new_success(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._write_records(run_dir / "judgments.jsonl", [{"status": "JUDGMENT"}])
            scope = service._credential_usage_scope(run_dir)
            self._append_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            self._append_records(
                run_dir / "judgments.jsonl",
                [{"status": "JUDGE_ERROR", "error_code": "TIMEOUT"}],
            )

            store = self._mark(run_dir, scope)

            self.assertEqual(
                store.mark_credential_used.call_args_list,
                [mock.call("target-credential", mock.ANY)],
            )

    def test_interrupted_execution_does_not_mark_new_failures(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            self._write_records(run_dir / "responses.jsonl", [{"status": "MODEL_RESPONSE"}])
            scope = service._credential_usage_scope(run_dir)
            self._append_records(
                run_dir / "responses.jsonl",
                [{"status": "TARGET_ERROR", "error_code": "NETWORK_ERROR"}],
            )

            store = self._mark(run_dir, scope)

            store.mark_credential_used.assert_not_called()

    def _mark(self, run_dir: Path, scope: object) -> mock.Mock:
        store = mock.Mock()
        request = SimpleNamespace(registry_root=run_dir / "registry")
        with mock.patch.object(service, "RegistryStore", return_value=store):
            service._mark_used_credentials(
                run_dir,
                scope,
                request,
                self._provider("target-credential"),
                self._provider("judge-credential"),
            )
        return store

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

    @staticmethod
    def _append_records(path: Path, records: list[dict[str, object]]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(json.dumps(record) for record in records) + "\n")
