"""Loaded-model selection and both mechanism CLI paths, without model calls."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from research import local_model_runtime as runtime
from scripts import generate_mechanism_cards as generate
from scripts import review_anonymous_mechanism_cards_local as review


GEMMA = {"type": "llm", "identifier": "gemma-runtime", "modelKey": "gemma-model-key",
         "format": "gguf", "architecture": "gemma4", "status": "idle", "contextLength": 33024}
OTHER = {"type": "llm", "identifier": "other-runtime", "modelKey": "other-model-key", "status": "generating"}
EMBEDDING = {"type": "embedding", "identifier": "embedding-only", "modelKey": "embedding-key"}


class LoadedModelSelectionTests(unittest.TestCase):
    def test_only_loaded_llm_defaults_to_runtime_identifier(self):
        self.assertEqual(runtime.select_loaded_model([GEMMA]), "gemma-runtime")
        self.assertEqual(runtime.select_loaded_model([EMBEDDING, GEMMA]), "gemma-runtime")
        # A loaded generator need not be idle to be a valid explicit choice.
        self.assertEqual(runtime.select_loaded_model([OTHER]), "other-runtime")

    def test_multiple_models_require_exact_identifier_or_key_and_return_identifier(self):
        models = [GEMMA, OTHER, EMBEDDING]
        with self.assertRaisesRegex(ValueError, "multiple LLMs"):
            runtime.select_loaded_model(models)
        for requested in ["gemma-runtime", "gemma-model-key"]:
            self.assertEqual(runtime.select_loaded_model(models, requested), "gemma-runtime")
        for requested in ["gemma", "GEMMA-RUNTIME", "missing", "embedding-only", "embedding-key"]:
            with self.subTest(requested=requested), self.assertRaisesRegex(ValueError, "exactly match"):
                runtime.select_loaded_model(models, requested)

    def test_no_loaded_llm_or_ambiguous_key_fails_closed(self):
        for models in [[], [EMBEDDING]]:
            for requested in [None, "gemma-model-key", "embedding-only"]:
                with self.subTest(models=models, requested=requested), self.assertRaisesRegex(ValueError, "no loaded type=llm"):
                    runtime.select_loaded_model(models, requested)
        duplicate_key = {**OTHER, "modelKey": GEMMA["modelKey"]}
        with self.assertRaisesRegex(ValueError, "exactly match one"):
            runtime.select_loaded_model([GEMMA, duplicate_key], GEMMA["modelKey"])

    def test_malformed_loaded_inventory_is_not_guessed(self):
        for models in [{"models": [GEMMA]}, [None], [{"type": "llm"}], [GEMMA, GEMMA]]:
            with self.subTest(models=models), self.assertRaises(ValueError):
                runtime.select_loaded_model(models)
        for requested in ["", " ", 7]:
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                runtime.select_loaded_model([GEMMA], requested)

    def test_resolver_runs_only_read_only_loaded_inventory_command(self):
        completed = subprocess.CompletedProcess(["lms", "ps", "--json"], 0, stdout=json.dumps([GEMMA]))
        with patch.object(runtime.subprocess, "run", return_value=completed) as run:
            self.assertEqual(runtime.resolve_loaded_model(GEMMA["modelKey"]), GEMMA["identifier"])
        run.assert_called_once_with(["lms", "ps", "--json"], check=True, capture_output=True,
                                    text=True, shell=False, timeout=15)

    def test_runtime_failure_or_invalid_json_does_not_fallback(self):
        errors = [FileNotFoundError("lms"), subprocess.TimeoutExpired("lms", 15), subprocess.CalledProcessError(1, "lms")]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch.object(runtime.subprocess, "run", side_effect=error) as run:
                with self.assertRaisesRegex(RuntimeError, "cannot inspect"):
                    runtime.resolve_loaded_model("gemma-runtime")
                self.assertEqual(run.call_count, 1)
        bad = subprocess.CompletedProcess("lms", 0, stdout="not-json")
        with patch.object(runtime.subprocess, "run", return_value=bad), self.assertRaisesRegex(ValueError, "invalid JSON"):
            runtime.resolve_loaded_model()

    def test_missing_or_invalid_response_receipt_fails_with_separate_evidence(self):
        for content in [None, "not-json", "[]", "{}"]:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                if content is not None:
                    (output / "receipt.json").write_text(content)
                with self.assertRaisesRegex(ValueError, "response model identity"):
                    runtime.verify_response_model(output, GEMMA["identifier"])
                check = json.loads((output / "model_identity_check.json").read_text())
                self.assertEqual(check["status"], "FAIL")
                if content is not None:
                    self.assertEqual((output / "receipt.json").read_text(), content)


class MechanismCliRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mechanism-runtime-synthetic-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.package = self.root / "package.json"
        self.package.write_text(json.dumps({"cards": [{"anonymous_id": "anon-1"}]}))
        self.cards = {"ideas": [{key: f"complete-{number}-{key}" for key in generate.KEYS} for number in range(3)]}
        self.reviews = {"reviews": [{"anonymous_id": "anon-1", "field_timing": "pass", "mechanism_overlap": "none",
            "simplest_alternative": "one field", "required_control": "fixed", "refutation_quality": "strong",
            "recommendation": "compile", "reason": "synthetic review"}]}

    def mocked_request_transport(self, result, *, response_model=None, finish_reason="stop"):
        """Exercise the existing request writer/receipt using a fake HTTP reply."""
        response = io.BytesIO(json.dumps({"model": response_model or GEMMA["identifier"], "choices": [{"finish_reason": finish_reason,
            "message": {"content": json.dumps(result)}}], "usage": {"total_tokens": 12}}).encode())
        return patch("urllib.request.build_opener", return_value=type("Opener", (), {
            "open": lambda _self, request, timeout: response})())

    def cli_args(self, module, output):
        return (["--package", str(self.package)] if module is review else []) + ["--output", str(output)]

    def test_both_cli_paths_default_or_explicit_key_record_resolved_identifier(self):
        for module, result, validated, seed, tokens in [
            (generate, self.cards, "validated_cards.json", 20260925, 2600),
            (review, self.reviews, "validated_review.json", 20260929, 5000),
        ]:
            for explicit in [False, True]:
                with self.subTest(module=module.__name__, explicit=explicit):
                    output = self.root / (module.__name__.split(".")[-1] + str(explicit))
                    inventory = [GEMMA, OTHER] if explicit else [EMBEDDING, GEMMA]
                    completed = subprocess.CompletedProcess("lms", 0, stdout=json.dumps(inventory))
                    args = self.cli_args(module, output) + (["--model", GEMMA["modelKey"], "--max-tokens", "6000"] if explicit else [])
                    with patch.object(runtime.subprocess, "run", return_value=completed), self.mocked_request_transport(result), redirect_stdout(io.StringIO()):
                        module.main(args)
                    request = json.loads((output / "request.json").read_text())
                    receipt = json.loads((output / "receipt.json").read_text())
                    self.assertEqual(request["model"], GEMMA["identifier"])
                    self.assertEqual(receipt["requested_model"], GEMMA["identifier"])
                    self.assertEqual(receipt["response_model"], GEMMA["identifier"])
                    self.assertEqual(receipt["endpoint"], "http://127.0.0.1:1234/v1")
                    self.assertFalse(receipt["cloud_fallback"])
                    self.assertEqual(request["seed"], seed)
                    self.assertEqual(request["max_tokens"], 6000 if explicit else tokens)
                    self.assertEqual([message["role"] for message in request["messages"]], ["system", "user"])
                    self.assertEqual(json.loads((output / validated).read_text()), result)
                    self.assertEqual(json.loads((output / "model_identity_check.json").read_text())["status"], "PASS")

    def test_both_cli_paths_reject_wrong_response_model_and_preserve_raw_evidence(self):
        completed = subprocess.CompletedProcess("lms", 0, stdout=json.dumps([GEMMA]))
        for module, result in [(generate, self.cards), (review, self.reviews)]:
            with self.subTest(module=module.__name__):
                output = self.root / module.__name__.split(".")[-1]
                with patch.object(runtime.subprocess, "run", return_value=completed), \
                     self.mocked_request_transport(result, response_model="unexpected-runtime"), \
                     self.assertRaisesRegex(ValueError, "response model identity does not match"):
                    module.main(self.cli_args(module, output))
                receipt = json.loads((output / "receipt.json").read_text())
                response = json.loads((output / "response.json").read_text())
                check = json.loads((output / "model_identity_check.json").read_text())
                self.assertEqual(receipt["response_model"], "unexpected-runtime")
                self.assertEqual(receipt["status"], "MODEL_RESPONSE_RECEIVED")
                self.assertEqual(response["model"], "unexpected-runtime")
                self.assertEqual(check["status"], "FAIL")
                self.assertEqual(check["expected_identifier"], GEMMA["identifier"])
                self.assertFalse(list(output.glob("validated_*.json")))

    def test_both_cli_paths_preserve_incomplete_response_and_failed_client_receipt(self):
        completed = subprocess.CompletedProcess("lms", 0, stdout=json.dumps([GEMMA]))
        for module in [generate, review]:
            with self.subTest(module=module.__name__):
                output = self.root / module.__name__.split(".")[-1]
                with patch.object(runtime.subprocess, "run", return_value=completed), \
                     self.mocked_request_transport({}, finish_reason="length"), \
                     self.assertRaisesRegex(ValueError, "incomplete model response"):
                    module.main(self.cli_args(module, output) + ["--max-tokens", "6000"])
                self.assertEqual(json.loads((output / "receipt.json").read_text())["status"], "FAILED")
                self.assertEqual(json.loads((output / "response.json").read_text())["choices"][0]["finish_reason"], "length")
                self.assertFalse(list(output.glob("validated_*.json")))

    def test_both_cli_paths_refuse_ambiguous_or_missing_model_without_output_or_http(self):
        for module in [generate, review]:
            for inventory, extra in [([GEMMA, OTHER], []), ([EMBEDDING], []), ([GEMMA], ["--model", "missing"])]:
                with self.subTest(module=module.__name__, inventory=inventory, extra=extra):
                    output = self.root / "must-not-exist"
                    completed = subprocess.CompletedProcess("lms", 0, stdout=json.dumps(inventory))
                    with patch.object(runtime.subprocess, "run", return_value=completed), patch("urllib.request.build_opener") as opener:
                        with self.assertRaises(ValueError):
                            module.main(self.cli_args(module, output) + extra)
                    opener.assert_not_called()
                    self.assertFalse(output.exists())

    def test_both_cli_paths_preserve_existing_outputs_and_validation(self):
        completed = subprocess.CompletedProcess("lms", 0, stdout=json.dumps([GEMMA]))
        for module in [generate, review]:
            with self.subTest(module=module.__name__):
                output = self.root / module.__name__.split(".")[-1]
                output.mkdir()
                marker = output / "original.json"
                marker.write_text("keep")
                with patch.object(runtime.subprocess, "run", return_value=completed), patch("urllib.request.build_opener") as opener:
                    with self.assertRaises(FileExistsError):
                        module.main(self.cli_args(module, output))
                opener.assert_not_called()
                self.assertEqual(marker.read_text(), "keep")
                self.assertEqual(list(output.iterdir()), [marker])
                invalid_output = self.root / (module.__name__.split(".")[-1] + "-invalid")
                invalid = {"ideas": []} if module is generate else {"reviews": []}
                with patch.object(runtime.subprocess, "run", return_value=completed), self.mocked_request_transport(invalid), self.assertRaises(ValueError):
                    module.main(self.cli_args(module, invalid_output))
                self.assertFalse((invalid_output / ("validated_cards.json" if module is generate else "validated_review.json")).exists())


if __name__ == "__main__":
    unittest.main()
