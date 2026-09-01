"""Unit tests for model behavioral eval execution and artifact semantics."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


class FakeProvider:
    provider_name = "fake"
    model = "fake-model"
    public_parameters = {"single_sample": True}

    def __init__(self, outputs: list[str | Exception], secret: str = "") -> None:
        self.outputs = list(outputs)
        self.secret = secret

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return runner.ProviderResult(value, response_id="fake-response")


def judgment_for(record: dict[str, Any], *, fail_first: bool = False) -> str:
    return json.dumps(
        {
            "case_id": record["case_id"],
            "criteria": [
                {
                    "criterion": item["criterion"],
                    "passed": not (fail_first and index == 0),
                    "reason": (
                        "Target 原文给出了该项要求对应的可核对内容"
                        if not (fail_first and index == 0)
                        else "Target 原文缺少首项要求对应的必要内容"
                    ),
                }
                for index, item in enumerate(record["criteria"])
            ]
        },
        ensure_ascii=False,
    )


class ModelEvalInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cases, criteria = runner.load_definitions()
        cls.all_prepared = runner.prepare_cases(cases, criteria)
        cls.prepared = cls.all_prepared[:2]

    def run_target(
        self,
        parent: Path,
        provider: FakeProvider,
        *,
        count: int = 1,
        name: str = "run",
        repository_dirty: bool | None = False,
    ) -> Path:
        run_dir = parent / "v1.6.0" / runner.API_RUNTIME_PROFILE / name
        runner.execute_run(
            self.prepared[:count],
            provider,
            run_dir,
            repository_sha="a" * 40,
            repository_dirty=repository_dirty,
            knowledge_pack_version="1.6.0",
            allow_dirty_debug=repository_dirty is True,
        )
        return run_dir

    def test_target_response_is_saved_incrementally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["可直接发送的回复"]))
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(response["status"], "MODEL_RESPONSE")
            self.assertEqual(response["response"], "可直接发送的回复")
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            self.assertIsNotNone(metadata["target_completed_at"])
            self.assertIsNone(metadata["completed_at"])
            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "TARGET_COMPLETE")

    def test_provider_failure_is_not_a_model_response(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(
                Path(temp_dir), FakeProvider([runner.ProviderError("provider unavailable")])
            )
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(response["status"], "TARGET_ERROR")
            self.assertIsNone(response["response"])
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertEqual(metadata["status"], "TARGET_PARTIAL")
            self.assertIsNone(metadata["target_completed_at"])
            self.assertIsNone(metadata["completed_at"])
            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "TARGET_PARTIAL")

    def test_timeout_and_invalid_response_have_distinct_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            timeout_dir = self.run_target(
                parent, FakeProvider([runner.ProviderTimeout("slow")]), name="timeout"
            )
            invalid_dir = self.run_target(parent, FakeProvider(["   "]), name="invalid")
            timeout = runner.load_jsonl(timeout_dir / "responses.jsonl")[0]
            invalid = runner.load_jsonl(invalid_dir / "responses.jsonl")[0]
            self.assertEqual(timeout["status"], "TARGET_ERROR")
            self.assertEqual(timeout["error_code"], "TIMEOUT")
            self.assertEqual(invalid["status"], "TARGET_ERROR")
            self.assertEqual(invalid["error_code"], "EMPTY_RESPONSE")

    def test_judgment_pass_fail_and_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(
                Path(temp_dir), FakeProvider(["回答一", "回答二"]), count=2
            )
            judge = FakeProvider(
                [judgment_for(self.prepared[0]), judgment_for(self.prepared[1], fail_first=True)]
            )
            counts = runner.execute_judge(
                run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
            )
            summary = runner.build_report(run_dir)
            self.assertEqual(counts, {"judged": 2, "judge_error": 0, "not_judged": 0})
            self.assertEqual(summary["behavioral_status"], "FAIL")
            self.assertEqual(summary["counts"]["passed_cases"], 1)
            self.assertEqual(summary["counts"]["failed_cases"], 1)
            self.assertEqual(len(summary["failed_cases"][0]["failed_criteria"]), 1)
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertEqual(metadata["status"], "COMPLETED")
            self.assertIsNotNone(metadata["target_completed_at"])
            self.assertIsNotNone(metadata["judge_completed_at"])
            self.assertIsNotNone(metadata["completed_at"])

    def test_malformed_judge_output_becomes_judge_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            counts = runner.execute_judge(
                run_dir,
                FakeProvider(["not json"]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            judgment = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
            summary = runner.build_report(run_dir)
            self.assertEqual(counts["judge_error"], 1)
            self.assertEqual(judgment["status"], "JUDGE_ERROR")
            self.assertEqual(summary["behavioral_status"], "NOT_EVALUABLE")

    def test_judge_executor_rejects_an_unscoped_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            with self.assertRaisesRegex(runner.ModelEvalError, "explicit planner-owned case scope"):
                runner.execute_judge(run_dir, FakeProvider([judgment_for(self.prepared[0])]))

    def test_current_artifact_rejects_missing_eval_schema_or_judge_thinking(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            definition = runner.load_json_object(run_dir / "eval-definition.json")
            definition.pop("eval_schema_version")
            with self.assertRaisesRegex(runner.ModelEvalError, "missing eval_schema_version"):
                runner.eval_identity_manifest(definition)

            runner.execute_judge(
                run_dir,
                FakeProvider([judgment_for(self.prepared[0])]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["judge"].pop("thinking")
            metadata["provider_manifest"]["judge"].pop("thinking")
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(
                runner.ModelEvalError, r"judge provider manifest is missing \['thinking'\]"
            ):
                runner.validate_result_artifacts(run_dir)

    def test_current_artifact_rejects_an_old_runner_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["schema_version"] = 2
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(runner.ModelEvalError, "unsupported run artifact schema"):
                runner.validate_result_artifacts(run_dir)

    def test_current_artifact_rejects_old_response_and_judgment_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            responses = runner.load_jsonl(run_dir / "responses.jsonl")
            responses[0]["schema_version"] = 2
            runner.write_jsonl(run_dir / "responses.jsonl", responses)
            with self.assertRaisesRegex(
                runner.ModelEvalError, "unsupported target response artifact schema"
            ):
                runner.validate_result_artifacts(run_dir)

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            runner.execute_judge(
                run_dir,
                FakeProvider([judgment_for(self.prepared[0])]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            judgments = runner.load_jsonl(run_dir / "judgments.jsonl")
            judgments[0]["schema_version"] = 2
            runner.write_jsonl(run_dir / "judgments.jsonl", judgments)
            with self.assertRaisesRegex(
                runner.ModelEvalError, "unsupported judgment artifact schema"
            ):
                runner.validate_result_artifacts(run_dir)

    def test_provider_secret_is_never_written(self) -> None:
        secret = "sk-test-must-never-be-written"
        real_provider_metadata = runner.provider_metadata(
            runner.OpenAIResponsesProvider(api_key=secret, model="explicit-model")
        )
        self.assertNotIn(secret, json.dumps(real_provider_metadata))
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"], secret=secret))
            serialized = "\n".join(
                path.read_text(encoding="utf-8") for path in run_dir.iterdir() if path.is_file()
            )
            self.assertNotIn(secret, serialized)

    def test_run_directory_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            self.run_target(parent, FakeProvider(["回答"]))
            with self.assertRaisesRegex(runner.ModelEvalError, "refusing to overwrite"):
                self.run_target(parent, FakeProvider(["另一个回答"]))

    def test_target_input_excludes_eval_metadata_for_reply_and_analysis(self) -> None:
        for mode in ("reply", "analysis"):
            record = {
                "case_id": f"secret-{mode}-case",
                "mode": mode,
                "criteria": [{"criterion": f"secret-{mode}-criterion"}],
                "input": f"真实用户输入-{mode}",
                "runtime": {"content": "CANONICAL RUNTIME\n"},
            }
            prompt = runner.target_input(record)
            self.assertEqual(
                prompt,
                f"CANONICAL RUNTIME\n\n## 用户输入\n\n真实用户输入-{mode}",
            )
            self.assertNotIn(record["case_id"], prompt)
            self.assertNotIn(record["criteria"][0]["criterion"], prompt)
            self.assertNotIn("当前评测模式", prompt)

    def test_pack_version_is_dynamic_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            info = root / "chatgpt-project" / "generated-knowledge" / "KNOWLEDGE_PACK_INFO.json"
            info.parent.mkdir(parents=True)
            info.write_text('{"pack_version":"v1.7.0"}\n', encoding="utf-8")
            self.assertEqual(runner.pack_version(root), "1.7.0")
            self.assertEqual(runner.results_root("1.7.0", root).name, "v1.7.0")

    def test_runtime_profiles_have_distinct_bundle_identity_and_paths(self) -> None:
        api_runtime = runner.runtime_snapshot(
            runner.API_RUNTIME_PROFILE, self.all_prepared
        )
        project_runtime = runner.runtime_snapshot(
            runner.CHATGPT_RUNTIME_PROFILE, self.all_prepared
        )
        self.assertNotEqual(
            runner.bundle_hash(self.all_prepared, api_runtime),
            runner.bundle_hash(self.all_prepared, project_runtime),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            self.assertEqual(
                runner.results_root("1.6.0", base, runner.API_RUNTIME_PROFILE).name,
                runner.API_RUNTIME_PROFILE,
            )
            self.assertEqual(
                runner.results_root(
                    "1.6.0", base, runner.CHATGPT_RUNTIME_PROFILE
                ).name,
                runner.CHATGPT_RUNTIME_PROFILE,
            )

    def test_missing_or_invalid_pack_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(runner.ModelEvalError, "Unable to determine"):
                runner.pack_version(root)
            info = root / "chatgpt-project" / "generated-knowledge" / "KNOWLEDGE_PACK_INFO.json"
            info.parent.mkdir(parents=True)
            info.write_text('{"pack_version":"unknown"}\n', encoding="utf-8")
            with self.assertRaisesRegex(runner.ModelEvalError, "Unable to determine"):
                runner.pack_version(root)

    def test_git_dirty_and_runner_revision_are_recorded(self) -> None:
        revision = subprocess.CompletedProcess([], 0, "c" * 40 + "\n", "")
        status = subprocess.CompletedProcess([], 0, " M scripts/run_model_evals.py\n", "")
        with mock.patch.object(runner, "run_git", side_effect=[revision, status]):
            fingerprint = runner.git_fingerprint()
        self.assertEqual(fingerprint, {"git_sha": "c" * 40, "git_dirty": True})
        normalized = Path(runner.__file__).read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        ).replace("\r", "\n")
        expected = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        self.assertEqual(runner.runner_revision(), expected)

    def test_dirty_worktree_is_prominent_in_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(
                Path(temp_dir), FakeProvider(["回答"]), repository_dirty=True
            )
            summary = runner.build_report(run_dir)
            self.assertTrue(summary["git_dirty"])
            self.assertIn(
                "DIRTY WORKTREE",
                (run_dir / "summary.md").read_text(encoding="utf-8"),
            )

    def test_summary_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            runner.execute_judge(
                run_dir,
                FakeProvider([judgment_for(self.prepared[0])]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            runner.build_report(run_dir)
            summary = runner.load_json_object(run_dir / "summary.json")
            summary["counts"]["passed_cases"] = 999
            runner.write_json(run_dir / "summary.json", summary)
            with self.assertRaisesRegex(runner.ModelEvalError, "summary field counts"):
                runner.validate_result_artifacts(run_dir)

    def test_provenance_snapshots_validate_when_untampered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            for filename in (
                "prepared.jsonl",
                "eval-definition.json",
                "runtime-snapshot.json",
                "source-snapshots.json",
            ):
                self.assertTrue((run_dir / filename).is_file(), filename)
            runner.validate_result_artifacts(run_dir)

    def test_wrong_bundle_hash_is_detected_even_when_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["bundle_hash"] = "sha256:" + "1" * 64
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(runner.ModelEvalError, "bundle_hash mismatch"):
                runner.validate_result_artifacts(run_dir)

    def test_wrong_eval_definition_hash_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["eval_definition_hash"] = "sha256:" + "2" * 64
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(
                runner.ModelEvalError, "eval_definition_hash mismatch"
            ):
                runner.validate_result_artifacts(run_dir)

    def test_modified_prepared_content_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            prepared = runner.load_jsonl(run_dir / "prepared.jsonl")
            prepared[0]["runtime"]["content"] += "\n篡改"
            runner.write_jsonl(run_dir / "prepared.jsonl", prepared)
            with self.assertRaisesRegex(runner.ModelEvalError, "bundle_hash mismatch"):
                runner.validate_result_artifacts(run_dir)

    def test_modified_runner_snapshot_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            snapshots = runner.load_json_object(run_dir / "source-snapshots.json")
            snapshots["runner"]["content"] += "\n# tampered"
            runner.write_json(run_dir / "source-snapshots.json", snapshots)
            with self.assertRaisesRegex(runner.ModelEvalError, "runner_revision mismatch"):
                runner.validate_result_artifacts(run_dir)

    def test_canonical_hash_ignores_json_key_order_and_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            prepared_path = run_dir / "prepared.jsonl"
            reordered = [
                dict(reversed(list(record.items())))
                for record in runner.load_jsonl(prepared_path)
            ]
            prepared_path.write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False, separators=(", ", ": "))
                    for record in reordered
                )
                + "\n",
                encoding="utf-8",
            )
            definition_path = run_dir / "eval-definition.json"
            definition = runner.load_json_object(definition_path)
            definition_path.write_text(
                json.dumps(
                    dict(reversed(list(definition.items()))),
                    ensure_ascii=False,
                    indent=4,
                )
                + "\n",
                encoding="utf-8",
            )
            runner.validate_result_artifacts(run_dir)

    def test_invalid_runtime_profile_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["runtime_profile"] = "unknown_profile"
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(runner.ModelEvalError, "invalid runtime_profile"):
                runner.validate_result_artifacts(run_dir)

    def test_cross_run_response_mix_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            first = self.run_target(parent, FakeProvider(["回答一"]), name="first")
            second = self.run_target(parent, FakeProvider(["回答二"]), name="second")
            runner.write_jsonl(
                first / "responses.jsonl",
                runner.load_jsonl(second / "responses.jsonl"),
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "artifact binding mismatch"):
                runner.validate_result_artifacts(first)

    def test_prepare_only_lifecycle(self) -> None:
        snapshots = runner.build_run_snapshots(
            self.prepared[:1], runner.API_RUNTIME_PROFILE
        )
        metadata = runner.new_run_metadata(
            self.prepared[:1],
            snapshots,
            run_id="prepared-only",
            target={"provider": "fake", "model": "fake", "parameters": {}},
            runtime_profile=runner.API_RUNTIME_PROFILE,
            repository_sha="a" * 40,
            repository_dirty=False,
        )
        self.assertEqual(metadata["status"], "PREPARED")
        self.assertIsNotNone(metadata["created_at"])
        self.assertIsNone(metadata["target_started_at"])
        self.assertIsNone(metadata["completed_at"])
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                Path(temp_dir)
                / "v1.6.0"
                / runner.API_RUNTIME_PROFILE
                / "prepared-only"
            )
            run_dir.mkdir(parents=True)
            runner.write_run_snapshots(run_dir, snapshots)
            runner.write_json(run_dir / "run.json", metadata, exclusive=True)
            runner.write_jsonl(run_dir / "responses.jsonl", [], exclusive=True)
            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "PREPARED")

    def test_summary_markdown_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            runner.execute_judge(
                run_dir,
                FakeProvider([judgment_for(self.prepared[0])]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            runner.build_report(run_dir)
            (run_dir / "summary.md").write_text("伪造摘要\n", encoding="utf-8")
            with self.assertRaisesRegex(runner.ModelEvalError, "summary.md differs"):
                runner.validate_result_artifacts(run_dir)

    def test_version_directory_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(Path(temp_dir), FakeProvider(["回答"]))
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["version_directory"] = "v9.9.9"
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(runner.ModelEvalError, "invalid version_directory"):
                runner.validate_result_artifacts(run_dir)

    def test_duplicate_and_unknown_response_cases_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            duplicate_dir = self.run_target(parent, FakeProvider(["回答"]), name="duplicate")
            responses = runner.load_jsonl(duplicate_dir / "responses.jsonl")
            runner.write_jsonl(duplicate_dir / "responses.jsonl", [*responses, responses[0]])
            with self.assertRaisesRegex(runner.ModelEvalError, "attempts are not append-only"):
                runner.validate_result_artifacts(duplicate_dir)

            unknown_dir = self.run_target(parent, FakeProvider(["回答"]), name="unknown")
            responses = runner.load_jsonl(unknown_dir / "responses.jsonl")
            responses[0]["case_id"] = "unknown-case"
            runner.write_jsonl(unknown_dir / "responses.jsonl", responses)
            with self.assertRaisesRegex(runner.ModelEvalError, "unknown case_id"):
                runner.validate_result_artifacts(unknown_dir)

    def test_missing_judgment_is_a_resumable_incomplete_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_target(
                Path(temp_dir), FakeProvider(["回答一", "回答二"]), count=2
            )
            runner.execute_judge(
                run_dir,
                FakeProvider([judgment_for(self.prepared[0]), judgment_for(self.prepared[1])]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            judgments = runner.load_jsonl(run_dir / "judgments.jsonl")
            runner.write_jsonl(run_dir / "judgments.jsonl", judgments[:1])
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["judge_phase_completed"] = False
            metadata["judge_completed_at"] = None
            metadata["completed_at"] = None
            runner.refresh_run_metadata(
                metadata,
                runner.load_jsonl(run_dir / "responses.jsonl"),
                judgments[:1],
            )
            runner.write_json(run_dir / "run.json", metadata)
            runner.validate_result_artifacts(run_dir)
            self.assertEqual(metadata["status"], "JUDGE_PARTIAL")
            self.assertEqual(metadata["counts"]["not_judged"], 1)

    def test_manual_response_import_is_partial_and_rejects_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / "manual"
            runner.export_manual_bundle(
                self.all_prepared, manual_dir, run_id="manual-partial"
            )
            response_file = root / "responses.jsonl"
            runner.write_jsonl(
                response_file,
                [{"case_id": self.all_prepared[0]["case_id"], "response": "原始回答"}],
            )
            run_dir, metadata, imported = runner.import_manual_responses(
                manual_dir, response_file, root / "results"
            )
            self.assertEqual(imported, 1)
            self.assertEqual(metadata["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(metadata["status"], "TARGET_PARTIAL")
            self.assertEqual(metadata["counts"]["model_response"], 1)
            self.assertEqual(metadata["counts"]["not_run"], len(self.all_prepared) - 1)
            self.assertIsNotNone(metadata["target_started_at"])
            self.assertIsNone(metadata["target_completed_at"])
            self.assertIsNone(metadata["completed_at"])
            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "TARGET_PARTIAL")
            self.assertEqual(run_dir.parent.name, runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(run_dir.parent.parent.name, "v1.6.0")
            with self.assertRaisesRegex(runner.ModelEvalError, "overwrite existing cases"):
                runner.import_manual_responses(manual_dir, response_file, root / "results")

    def test_manual_judgment_import_reuses_criterion_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / "manual"
            runner.export_manual_bundle(self.all_prepared, manual_dir, run_id="manual-judge")
            response_file = root / "responses.jsonl"
            runner.write_jsonl(
                response_file,
                [
                    {
                        "case_id": record["case_id"],
                        "response": f"原始回答-{index}",
                    }
                    for index, record in enumerate(self.all_prepared, start=1)
                ],
            )
            run_dir, _, _ = runner.import_manual_responses(
                manual_dir, response_file, root / "results"
            )
            invalid = root / "invalid-judgment.jsonl"
            runner.write_jsonl(
                invalid,
                [{"case_id": self.all_prepared[0]["case_id"], "criteria": []}],
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "cover every required"):
                runner.import_manual_judgments(
                    run_dir, invalid, judge_mode="manual_human"
                )
            duplicate = root / "duplicate-judgment.jsonl"
            duplicate_payload = json.loads(judgment_for(self.all_prepared[0]))
            duplicate_payload["case_id"] = self.all_prepared[0]["case_id"]
            duplicate_payload["criteria"].append(duplicate_payload["criteria"][0])
            runner.write_jsonl(duplicate, [duplicate_payload])
            with self.assertRaisesRegex(runner.ModelEvalError, "duplicate criteria"):
                runner.import_manual_judgments(
                    run_dir, duplicate, judge_mode="manual_human"
                )
            valid = root / "valid-judgment.jsonl"
            runner.write_jsonl(
                valid,
                [json.loads(judgment_for(self.all_prepared[0])) | {"case_id": self.all_prepared[0]["case_id"]}],
            )
            metadata, imported = runner.import_manual_judgments(
                run_dir, valid, judge_mode="manual_human"
            )
            self.assertEqual(imported, 1)
            self.assertEqual(metadata["judge"]["provider"], "human")
            self.assertEqual(metadata["counts"]["judged"], 1)
            self.assertEqual(metadata["status"], "JUDGE_PARTIAL")
            self.assertIsNotNone(metadata["judge_started_at"])
            self.assertIsNone(metadata["judge_completed_at"])
            self.assertIsNone(metadata["completed_at"])
            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "JUDGE_PARTIAL")
            runner.validate_result_artifacts(run_dir)


if __name__ == "__main__":
    unittest.main()
