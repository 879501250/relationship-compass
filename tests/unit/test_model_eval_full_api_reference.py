"""Full API reference runner identity, recovery, and acceptance tests."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


class HTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> "HTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class SequenceProvider:
    provider_name = "sequence-provider"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, outputs: list[str | Exception], *, model: str = "model-a") -> None:
        self.outputs = list(outputs)
        self.model = model
        self.calls = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return runner.ProviderResult(
            text=value,
            response_id=f"response-{self.calls}",
            reported_model=self.model,
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "reasoning_tokens": 2,
                "cached_tokens": 3,
            },
        )


def passing_judgment(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": record["case_id"],
            "criteria": [
                {
                    "criterion": item["criterion"],
                    "passed": True,
                    "reason": "Target 原文包含该项所需的具体可核对内容",
                }
                for item in record["criteria"]
            ],
        },
        ensure_ascii=False,
    )


class FullAPIReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cases, criteria = runner.load_definitions()
        cls.all_prepared = runner.prepare_cases(cases, criteria)
        cls.prepared = cls.all_prepared[:2]

    @staticmethod
    def run_dir(root: Path, name: str) -> Path:
        return root / "v1.6.0" / runner.API_RUNTIME_PROFILE / name

    def test_target_resume_is_append_only_and_skips_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "target-resume")
            first = SequenceProvider(
                [
                    "first answer",
                    runner.ProviderError(
                        "rate limited", code="RATE_LIMIT", retryable=True
                    ),
                ]
            )
            metadata = runner.execute_run(
                self.prepared,
                first,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.assertEqual(metadata["status"], "TARGET_PARTIAL")
            second = SequenceProvider(["second answer"])
            metadata = runner.execute_run(
                self.prepared,
                second,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
                resume=True,
            )
            self.assertEqual(second.calls, 1)
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            attempts = runner.load_jsonl(run_dir / "responses.jsonl")
            self.assertEqual(
                [(item["case_id"], item["attempt"]) for item in attempts],
                [
                    (self.prepared[0]["case_id"], 1),
                    (self.prepared[1]["case_id"], 1),
                    (self.prepared[1]["case_id"], 2),
                ],
            )
            runner.validate_result_artifacts(run_dir)

    def test_concurrent_target_execution_preserves_case_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "target-concurrency")
            provider = SequenceProvider(["first answer", "second answer"])
            metadata = runner.execute_run(
                self.prepared,
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
                concurrency=2,
            )
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            responses = runner.load_jsonl(run_dir / "responses.jsonl")
            self.assertEqual(
                [item["case_id"] for item in responses],
                [item["case_id"] for item in self.prepared],
            )
            runner.validate_result_artifacts(run_dir)

    def test_target_resume_refuses_model_and_workload_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "resume-mismatch")
            runner.execute_run(
                self.prepared[:1],
                SequenceProvider(
                    [runner.ProviderError("network", code="NETWORK_ERROR", retryable=True)]
                ),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "configuration mismatch"):
                runner.execute_run(
                    self.prepared[:1],
                    SequenceProvider(["answer"], model="model-b"),
                    run_dir,
                    resume=True,
                )

    def test_target_resume_refuses_endpoint_reasoning_and_eval_changes(self) -> None:
        def failing_opener(*_args: Any, **_kwargs: Any) -> Any:
            raise urllib.error.URLError("offline")

        variants = (
            {
                "base_url": "https://relay.example/v1",
                "declared_upstream_vendor": "Relay Vendor",
                "provenance_type": "declared_relay",
                "reasoning_effort": "low",
            },
            {
                "base_url": runner.DEFAULT_OPENAI_BASE_URL,
                "reasoning_effort": "high",
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index, variant in enumerate(variants, 1):
                run_dir = self.run_dir(root, f"config-{index}")
                runner.execute_run(
                    self.prepared[:1],
                    runner.OpenAIResponsesProvider(
                        api_key="test-only",
                        model="model-a",
                        reasoning_effort="low",
                        max_retries=0,
                        urlopen=failing_opener,
                    ),
                    run_dir,
                    repository_sha="a" * 40,
                    repository_dirty=False,
                )
                with self.assertRaisesRegex(
                    runner.ModelEvalError, "configuration mismatch"
                ):
                    runner.execute_run(
                        self.prepared[:1],
                        runner.OpenAIResponsesProvider(
                            api_key="test-only",
                            model="model-a",
                            max_retries=0,
                            urlopen=failing_opener,
                            **variant,
                        ),
                        run_dir,
                        resume=True,
                    )

            eval_run = self.run_dir(root, "eval-change")
            provider = SequenceProvider(
                [runner.ProviderError("network", code="NETWORK_ERROR", retryable=True)]
            )
            runner.execute_run(
                self.prepared[:1],
                provider,
                eval_run,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            changed_eval = runner.eval_definition_snapshot()
            changed_eval["judge"]["calibration"] += " changed"
            with mock.patch.object(
                runner, "eval_definition_snapshot", return_value=changed_eval
            ), self.assertRaisesRegex(runner.ModelEvalError, "Eval definition changed"):
                runner.execute_run(
                    self.prepared[:1],
                    SequenceProvider(["answer"]),
                    eval_run,
                    resume=True,
                )
            with self.assertRaisesRegex(runner.ModelEvalError, "prepared Eval/SUT changed"):
                runner.execute_run(
                    self.prepared,
                    SequenceProvider(["answer"]),
                    run_dir,
                    resume=True,
                )

    def test_model_identity_states_are_separate_from_endpoint_identity(self) -> None:
        manifest = runner.provider_metadata(SequenceProvider([]))
        self.assertEqual(
            runner.model_identity_from_records(
                manifest, [{"reported_model": "model-a"}]
            )["status"],
            "MATCHED",
        )
        self.assertEqual(
            runner.model_identity_from_records(manifest, [])["status"], "MISSING"
        )
        self.assertEqual(
            runner.model_identity_from_records(
                manifest,
                [{"reported_model": "model-a"}, {"reported_model": "model-b"}],
            )["status"],
            "MULTIPLE",
        )
        mismatch = runner.model_identity_from_records(
            manifest,
            [{"error_code": "MODEL_IDENTITY_MISMATCH", "reported_model": "model-b"}],
        )
        self.assertEqual(mismatch["status"], "MISMATCH")
        self.assertFalse(manifest["provider_identity"]["endpoint_verified"])

    def test_manual_target_model_is_user_reported_not_provider_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / "manual"
            runner.export_manual_bundle(
                self.all_prepared,
                manual_dir,
                run_id="manual-identity",
                target_model="user-visible-label",
            )
            import_path = root / "responses.jsonl"
            runner.write_jsonl(
                import_path,
                [{"case_id": self.all_prepared[0]["case_id"], "response": "answer"}],
            )
            run_dir, metadata, _ = runner.import_manual_responses(
                manual_dir, import_path, root / "results"
            )
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertIsNone(response["requested_model"])
            self.assertIsNone(response["reported_model"])
            self.assertEqual(response["user_reported_model"], "user-visible-label")
            self.assertEqual(
                metadata["identities"]["target"]["model_identity"]["status"],
                "USER_REPORTED",
            )

    def test_manual_target_fallback_marks_mixed_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self.run_dir(root, "mixed-target")
            runner.execute_run(
                self.prepared,
                SequenceProvider(
                    [
                        "api answer",
                        runner.ProviderError("auth", code="AUTH_ERROR", retryable=False),
                    ]
                ),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            export_dir = root / "manual-fallback"
            self.assertEqual(
                runner.export_manual_target_remaining(run_dir, export_dir), 1
            )
            import_path = root / "manual-responses.jsonl"
            runner.write_jsonl(
                import_path,
                [{"case_id": self.prepared[1]["case_id"], "response": "manual answer"}],
            )
            metadata, imported = runner.import_manual_target_remaining(
                run_dir, import_path, user_reported_model="manual-label"
            )
            self.assertEqual(imported, 1)
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            self.assertEqual(metadata["execution"]["target"], "MIXED_EXECUTION")
            runner.validate_result_artifacts(run_dir)

    def test_manual_judge_export_handles_duplicate_api_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self.run_dir(root, "judge-attempts")
            runner.execute_run(
                self.prepared,
                SequenceProvider(["one", "two"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_judge(
                run_dir,
                SequenceProvider(
                    [
                        runner.ProviderError("network", code="NETWORK_ERROR", retryable=True),
                        runner.ProviderError("network", code="NETWORK_ERROR", retryable=True),
                    ],
                    model="judge-model",
                ),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            runner.execute_judge(
                run_dir,
                SequenceProvider(
                    [
                        passing_judgment(self.prepared[0]),
                        runner.ProviderError("network", code="NETWORK_ERROR", retryable=True),
                    ],
                    model="judge-model",
                ),
                resume=True,
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            output = root / "manual-judge"
            self.assertEqual(runner.export_manual_judge(run_dir, output), 1)
            import_path = root / "manual-judgment.jsonl"
            runner.write_jsonl(
                import_path,
                [
                    json.loads(passing_judgment(self.prepared[1]))
                ],
            )
            metadata, imported = runner.import_manual_judgments(
                run_dir,
                import_path,
                judge_mode="manual_chatgpt",
                judge_model="manual-judge-label",
            )
            self.assertEqual(imported, 1)
            self.assertEqual(metadata["status"], "COMPLETED")
            self.assertEqual(metadata["execution"]["judge"], "MIXED_EXECUTION")
            runner.validate_result_artifacts(run_dir)

    def test_acceptance_is_separate_and_does_not_modify_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "acceptance")
            runner.execute_run(
                self.prepared[:1],
                SequenceProvider(["answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_judge(
                run_dir,
                SequenceProvider(
                    [passing_judgment(self.prepared[0])], model="judge-model"
                ),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            runner.build_report(run_dir)
            immutable = [
                "run.json",
                "responses.jsonl",
                "judgments.jsonl",
                "summary.json",
                "summary.md",
            ]
            before = {
                name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                for name in immutable
            }
            pending = runner.effective_reference_status(run_dir)
            self.assertEqual(pending["acceptance_status"], "PENDING_HUMAN_REVIEW")
            self.assertEqual(
                pending["effective_reference_qualification"],
                "REFERENCE_PROVISIONAL",
            )
            acceptance = runner.accept_reference(run_dir, notes="reviewed")
            after = {
                name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                for name in immutable
            }
            self.assertEqual(after, before)
            self.assertTrue(acceptance["accepted"])
            accepted = runner.effective_reference_status(run_dir)
            self.assertEqual(accepted["acceptance_status"], "ACCEPTED")
            self.assertEqual(
                accepted["effective_reference_qualification"],
                "REFERENCE_PROVISIONAL",
            )
            args = runner.build_parser().parse_args(
                ["reference-status", "--run-dir", str(run_dir)]
            )
            with mock.patch("sys.stdout", new=io.StringIO()) as captured:
                self.assertEqual(runner.command_reference_status(args), 0)
                self.assertEqual(
                    json.loads(captured.getvalue())["acceptance_status"], "ACCEPTED"
                )
            runner.validate_result_artifacts(run_dir)
            acceptance["summary_hash"] = "sha256:" + "0" * 64
            runner.write_json(run_dir / "acceptance.json", acceptance)
            with self.assertRaisesRegex(runner.ModelEvalError, "summary_hash"):
                runner.effective_reference_status(run_dir)

    def test_canonical_prompt_and_request_envelope_hashes_are_recorded(self) -> None:
        def opener(*_args: Any, **_kwargs: Any) -> HTTPResponse:
            return HTTPResponse(
                {
                    "id": "r1",
                    "status": "completed",
                    "model": "model-a",
                    "output_text": "answer",
                }
            )

        provider = runner.OpenAIResponsesProvider(
            api_key="test-only", model="model-a", urlopen=opener
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "prompt-hash")
            runner.execute_run(
                self.prepared[:1],
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(
                response["target_prompt_version"], runner.TARGET_PROMPT_VERSION
            )
            for field in (
                "canonical_target_prompt_hash",
                "system_instructions_hash",
                "runtime_content_hash",
                "user_input_hash",
                "request_envelope_hash",
            ):
                self.assertRegex(response[field], r"^sha256:[0-9a-f]{64}$")

    def test_same_eval_different_sut_is_comparable(self) -> None:
        base = {
            "eval_identity": {
                "eval_schema_version": "v",
                "eval_definition_hash": "eval",
                "cases_hash": "cases",
                "rubric_hash": "rubric",
                "judge_prompt_hash": "judge",
                "suite_metadata_hash": "suites",
            },
            "sut_identity": {
                "product_version": "1.6.0",
                "git_sha": "a",
                "runtime_profile": "api_canonical",
                "skill_instructions_hash": "skill-a",
                "generated_knowledge_hash": None,
                "runtime_snapshot_hash": "runtime-a",
                "sut_bundle_hash": "sut-a",
            },
            "runtime_profile": "api_canonical",
            "provider_manifest": {"target": {}, "judge": {}},
            "execution": {"target": "PURE_API", "judge": "PURE_API"},
            "target_execution": {"target_prompt_version": runner.TARGET_PROMPT_VERSION},
            "samples_per_case": 1,
        }
        changed = json.loads(json.dumps(base))
        changed["sut_identity"].update(
            {
                "product_version": "1.7.0",
                "git_sha": "b",
                "skill_instructions_hash": "skill-b",
                "runtime_snapshot_hash": "runtime-b",
                "sut_bundle_hash": "sut-b",
            }
        )
        result = runner.assess_comparability(base, changed)
        self.assertEqual(result["level"], "COMPARABLE")
        self.assertIn("sut_bundle_hash", result["differences"]["sut"])
        changed["eval_identity"]["rubric_hash"] = "rubric-b"
        self.assertEqual(
            runner.assess_comparability(base, changed)["level"], "NOT_COMPARABLE"
        )

    def test_dirty_formal_run_is_refused_without_explicit_debug_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "dirty")
            with self.assertRaisesRegex(runner.ModelEvalError, "clean Git worktree"):
                runner.execute_run(
                    self.prepared[:1],
                    SequenceProvider(["answer"]),
                    run_dir,
                    repository_sha="a" * 40,
                    repository_dirty=True,
                )

    def test_target_error_path_does_not_persist_keys_or_query_secrets(self) -> None:
        def auth_error(*_args: Any, **_kwargs: Any) -> Any:
            raise urllib.error.HTTPError(
                "https://relay.example/v1/responses",
                401,
                "unauthorized",
                None,
                io.BytesIO(b"super-secret query-secret"),
            )

        provider = runner.OpenAIResponsesProvider(
            api_key="super-secret",
            model="model-a",
            base_url="https://relay.example/v1?access_token=query-secret",
            declared_upstream_vendor="Relay Vendor",
            provenance_type="declared_relay",
            max_retries=0,
            urlopen=auth_error,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "secret-error")
            runner.execute_run(
                self.prepared[:1],
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_dir.iterdir()
                if path.is_file()
            )
            self.assertNotIn("super-secret", persisted)
            self.assertNotIn("query-secret", persisted)
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(response["error_code"], "AUTH_ERROR")

    def test_hidden_reasoning_is_not_returned_as_behavioral_text(self) -> None:
        provider = runner.OpenAICompatibleChatProvider(
            api_key="test-only",
            model="model-a",
            base_url="https://relay.example/v1",
            urlopen=lambda *_args, **_kwargs: HTTPResponse(
                {
                    "id": "c1",
                    "model": "model-a",
                    "choices": [
                        {
                            "message": {
                                "content": "visible answer",
                                "reasoning_content": "hidden trace",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
        )
        result = provider.generate(instructions="system", input_text="input")
        self.assertEqual(result.text, "visible answer")
        self.assertNotIn("hidden trace", json.dumps(result.__dict__))

    def test_provider_check_is_zero_request_and_smoke_stays_in_work(self) -> None:
        preflight_provider = SequenceProvider([])
        preflight_args = runner.build_parser().parse_args(
            ["provider-check", "--role", "target"]
        )
        with mock.patch.object(
            runner, "create_provider", return_value=preflight_provider
        ), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(runner.command_provider_check(preflight_args), 0)
        self.assertEqual(preflight_provider.calls, 0)

        work_root = ROOT / ".work"
        work_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory(
            dir=work_root
        ) as debug_dir:
            prepared_path = Path(temp_dir) / "prepared.jsonl"
            runner.write_jsonl(prepared_path, self.prepared[:1])
            case_id = self.prepared[0]["case_id"]
            for role, output_text in (
                ("target", "smoke answer"),
                ("judge", passing_judgment(self.prepared[0])),
            ):
                provider = SequenceProvider(
                    [output_text], model=f"{role}-smoke-model"
                )
                output = Path(debug_dir) / f"{role}.json"
                args = runner.build_parser().parse_args(
                    [
                        "smoke",
                        "--role",
                        role,
                        "--case-id",
                        case_id,
                        "--prepared",
                        str(prepared_path),
                        "--output",
                        str(output),
                    ]
                )
                with mock.patch.object(
                    runner, "create_provider", return_value=provider
                ), mock.patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(runner.command_smoke(args), 0)
                evidence = runner.load_json_object(output)
                self.assertTrue(evidence["debug_only"])
                self.assertFalse(evidence["formal_result_modified"])
                self.assertEqual(provider.calls, 1)


if __name__ == "__main__":
    unittest.main()
