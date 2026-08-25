"""Small fake-provider E2E for prepare -> run -> judge -> report."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


class RecordingFakeProvider:
    provider_name = "fake-e2e"
    model = "fake-e2e-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        self.calls.append(
            {
                "instructions": instructions,
                "input": input_text,
                "structured": response_schema is not None,
            }
        )
        return runner.ProviderResult(self.outputs.pop(0))


def all_pass(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "criteria": [
                {"criterion": item["criterion"], "passed": True, "reason": "满足"}
                for item in record["criteria"]
            ]
        },
        ensure_ascii=False,
    )


class ModelEvalPipelineTests(unittest.TestCase):
    def test_two_case_fake_provider_pipeline(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:2], criteria)
        self.assertIn("SKILL.md", prepared[0]["runtime"]["sources"])
        self.assertIn("relationship-compass", prepared[0]["runtime"]["content"])

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                Path(temp_dir)
                / "v1.6.0"
                / runner.API_RUNTIME_PROFILE
                / "fake-e2e-run"
            )
            target = RecordingFakeProvider(["回复一", "回复二"])
            runner.execute_run(
                prepared,
                target,
                run_dir,
                repository_sha="b" * 40,
                repository_dirty=False,
                knowledge_pack_version="1.6.0",
            )
            judge = RecordingFakeProvider([all_pass(prepared[0]), all_pass(prepared[1])])
            runner.execute_judge(run_dir, judge)
            summary = runner.build_report(run_dir)

            self.assertEqual(len(target.calls), 2)
            self.assertTrue(all(not call["structured"] for call in target.calls))
            self.assertEqual(len(judge.calls), 2)
            self.assertTrue(all(call["structured"] for call in judge.calls))
            self.assertEqual(summary["completion_status"], "COMPLETED")
            self.assertEqual(summary["behavioral_status"], "PASS")
            self.assertEqual(summary["runtime_profile"], runner.API_RUNTIME_PROFILE)
            self.assertFalse(summary["baseline"])
            for call, record in zip(target.calls, prepared, strict=True):
                self.assertEqual(call["input"], runner.target_input(record))
                self.assertNotIn(record["case_id"], call["input"])
                self.assertNotIn("当前评测模式", call["input"])
                for criterion in record["criteria"]:
                    self.assertNotIn(criterion["criterion"], call["input"])
            runner.validate_result_artifacts(run_dir)
            for filename in (
                "run.json",
                "prepared.jsonl",
                "eval-definition.json",
                "runtime-snapshot.json",
                "source-snapshots.json",
                "responses.jsonl",
                "judgments.jsonl",
                "summary.json",
            ):
                self.assertTrue((run_dir / filename).is_file(), filename)

    def test_full_fake_manual_pipeline(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases, criteria)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / ".work" / "manual-full"
            runner.export_manual_bundle(
                prepared,
                manual_dir,
                run_id="manual-full-e2e",
                target_model="chatgpt-project",
            )
            runtime_snapshot = runner.load_json_object(
                manual_dir / "runtime-snapshot.json"
            )
            self.assertEqual(
                runtime_snapshot["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE
            )
            self.assertIn("project_instructions", runtime_snapshot)
            self.assertGreater(len(runtime_snapshot["knowledge"]), 0)

            target_files = sorted((manual_dir / "target").glob("*.md"))
            self.assertEqual(len(target_files), len(prepared))
            for path, record in zip(target_files, prepared, strict=True):
                text = path.read_text(encoding="utf-8")
                self.assertIn(record["input"], text)
                self.assertNotIn(record["case_id"], text)
                self.assertNotIn("当前评测模式", text)
                for criterion in record["criteria"]:
                    self.assertNotIn(criterion["criterion"], text)

            response_file = root / "manual-responses.jsonl"
            runner.write_jsonl(
                response_file,
                [
                    {"case_id": record["case_id"], "response": f"回答-{index:02d}"}
                    for index, record in enumerate(prepared, start=1)
                ],
            )
            run_dir, metadata, imported = runner.import_manual_responses(
                manual_dir, response_file, root / "results"
            )
            self.assertEqual(imported, len(prepared))
            self.assertEqual(metadata["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            self.assertEqual(metadata["counts"]["model_response"], len(prepared))

            judge_dir = root / ".work" / "judge-full"
            runner.export_manual_judge(run_dir, judge_dir)
            judge_files = sorted(
                path
                for path in judge_dir.glob("*.md")
                if path.name != "INSTRUCTIONS.md"
            )
            self.assertEqual(len(judge_files), len(prepared))
            first_judge_prompt = judge_files[0].read_text(encoding="utf-8")
            self.assertIn(prepared[0]["input"], first_judge_prompt)
            self.assertIn("回答-01", first_judge_prompt)
            for criterion in prepared[0]["criteria"]:
                self.assertIn(criterion["criterion"], first_judge_prompt)

            judgment_file = root / "manual-judgments.jsonl"
            runner.write_jsonl(
                judgment_file,
                [
                    json.loads(all_pass(record)) | {"case_id": record["case_id"]}
                    for record in prepared
                ],
            )
            metadata, imported = runner.import_manual_judgments(
                run_dir,
                judgment_file,
                judge_mode="manual_chatgpt",
                judge_model="chatgpt-project-independent-judge",
            )
            self.assertEqual(imported, len(prepared))
            self.assertEqual(metadata["status"], "COMPLETED")
            self.assertEqual(metadata["judge"]["provider"], "chatgpt_web_manual")
            self.assertEqual(
                metadata["judge"]["parameters"]["judge_mode"], "manual_chatgpt"
            )

            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "COMPLETED")
            self.assertEqual(summary["behavioral_status"], "PASS")
            self.assertEqual(summary["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(summary["counts"]["passed_cases"], len(prepared))
            runner.validate_result_artifacts(run_dir)


if __name__ == "__main__":
    unittest.main()
