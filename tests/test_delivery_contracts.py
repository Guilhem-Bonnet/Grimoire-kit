"""Tests for delivery-contracts.py — Story 4.4."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "framework" / "tools" / "delivery-contracts.py"


def _load():
    mod_name = "delivery_contracts"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


dc = _load()


class TestConstants(unittest.TestCase):
    def test_version(self):
        self.assertTrue(dc.DELIVERY_CONTRACTS_VERSION)

    def test_builtin_contracts(self):
        self.assertGreater(len(dc.BUILTIN_CONTRACTS), 3)
        self.assertIn("code-review", dc.BUILTIN_CONTRACTS)
        self.assertIn("architecture-review", dc.BUILTIN_CONTRACTS)


class TestContractField(unittest.TestCase):
    def test_create(self):
        f = dc.ContractField(name="title", field_type="string", required=True)
        self.assertEqual(f.name, "title")
        self.assertTrue(f.required)

    def test_defaults(self):
        f = dc.ContractField(name="x", field_type="string")
        self.assertFalse(f.required)
        self.assertEqual(f.description, "")


class TestContractSchema(unittest.TestCase):
    def test_to_json_schema(self):
        schema = dc.ContractSchema(fields=[
            dc.ContractField(name="title", field_type="string", required=True),
            dc.ContractField(name="score", field_type="integer"),
        ])
        js = schema.to_json_schema()
        self.assertEqual(js["type"], "object")
        self.assertIn("title", js["properties"])
        self.assertIn("title", js["required"])

    def test_from_json_schema(self):
        raw = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The title"},
                "count": {"type": "integer"},
            },
            "required": ["title"],
        }
        schema = dc.ContractSchema.from_json_schema(raw)
        self.assertEqual(len(schema.fields), 2)
        title_field = next(f for f in schema.fields if f.name == "title")
        self.assertTrue(title_field.required)

    def test_roundtrip(self):
        schema = dc.ContractSchema(fields=[
            dc.ContractField(name="a", field_type="string", required=True),
            dc.ContractField(name="b", field_type="number"),
        ])
        js = schema.to_json_schema()
        restored = dc.ContractSchema.from_json_schema(js)
        self.assertEqual(len(restored.fields), 2)


class TestDeliveryContract(unittest.TestCase):
    def test_create(self):
        c = dc.DeliveryContract(
            name="test-contract",
            description="A test",
            source_agent="dev",
            target_agent="qa",
        )
        self.assertEqual(c.name, "test-contract")
        self.assertTrue(c.version)

    def test_to_dict_from_dict(self):
        c = dc.DeliveryContract(
            name="my-contract",
            description="Test",
            source_agent="dev",
            target_agent="qa",
            tags=["test"],
        )
        d = c.to_dict()
        restored = dc.DeliveryContract.from_dict(d)
        self.assertEqual(restored.name, "my-contract")
        self.assertEqual(restored.tags, ["test"])


class TestSchemaValidator(unittest.TestCase):
    def setUp(self):
        self.validator = dc.SchemaValidator()

    def test_valid_string(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        result = self.validator.validate({"name": "hello"}, schema)
        self.assertTrue(result.valid)

    def test_missing_required(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        result = self.validator.validate({}, schema)
        self.assertFalse(result.valid)
        self.assertGreater(len(result.errors), 0)

    def test_wrong_type(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        result = self.validator.validate({"count": "notint"}, schema)
        self.assertFalse(result.valid)

    def test_min_length(self):
        schema = {"type": "object", "properties": {"name": {"type": "string", "minLength": 3}}}
        result = self.validator.validate({"name": "ab"}, schema)
        self.assertFalse(result.valid)

    def test_max_length(self):
        schema = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 5}}}
        result = self.validator.validate({"name": "toolong"}, schema)
        self.assertFalse(result.valid)

    def test_minimum(self):
        schema = {"type": "object", "properties": {"score": {"type": "number", "minimum": 0}}}
        result = self.validator.validate({"score": -1}, schema)
        self.assertFalse(result.valid)

    def test_maximum(self):
        schema = {"type": "object", "properties": {"score": {"type": "number", "maximum": 100}}}
        result = self.validator.validate({"score": 101}, schema)
        self.assertFalse(result.valid)

    def test_enum(self):
        schema = {"type": "object", "properties": {"status": {"type": "string", "enum": ["ok", "fail"]}}}
        result = self.validator.validate({"status": "bad"}, schema)
        self.assertFalse(result.valid)

    def test_pattern(self):
        schema = {"type": "object", "properties": {"id": {"type": "string", "pattern": "^[A-Z]+$"}}}
        result = self.validator.validate({"id": "abc"}, schema)
        self.assertFalse(result.valid)

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "meta": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                }
            },
        }
        result = self.validator.validate({"meta": {}}, schema)
        self.assertFalse(result.valid)

    def test_valid_complex(self):
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1},
                "score": {"type": "number", "minimum": 0, "maximum": 10},
                "status": {"type": "string", "enum": ["draft", "final"]},
            },
            "required": ["title", "score"],
        }
        result = self.validator.validate({"title": "Test", "score": 5, "status": "draft"}, schema)
        self.assertTrue(result.valid)


class TestContractRegistry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry = dc.ContractRegistry(Path(self.tmpdir))

    def test_has_builtins(self):
        names = self.registry.list_names()
        self.assertGreater(len(names), 0)

    def test_get_contract(self):
        c = self.registry.get("code-review")
        self.assertIsNotNone(c)
        self.assertEqual(c.name, "code-review")

    def test_get_nonexistent(self):
        c = self.registry.get("nonexistent")
        self.assertIsNone(c)

    def test_validate_input_builtin(self):
        contract = self.registry.get("code-review")
        input_schema = contract.input_schema.to_json_schema()
        required_fields = input_schema.get("required", [])
        payload = {}
        for field_name in required_fields:
            prop = input_schema.get("properties", {}).get(field_name, {})
            field_type = prop.get("type", "string")
            if field_type == "string":
                payload[field_name] = "test value"
            elif field_type == "integer":
                payload[field_name] = 1
            elif field_type == "number":
                payload[field_name] = 1.0
            elif field_type == "array":
                payload[field_name] = []
            elif field_type == "object":
                payload[field_name] = {}
        result = self.registry.validate_input("code-review", payload)
        self.assertTrue(result.valid)

    def test_validate_input_invalid(self):
        result = self.registry.validate_input("code-review", {})
        self.assertFalse(result.valid)

    def test_validate_output_builtin(self):
        payload = {"status": "approved", "issues": []}
        result = self.registry.validate_output("code-review", payload)
        self.assertTrue(result.valid)

    def test_generate_template(self):
        tpl = self.registry.generate_template("architecture-review")
        self.assertIsNotNone(tpl)
        self.assertIn("name", tpl)
        self.assertIn("input_schema", tpl)
        self.assertIn("output_schema", tpl)

    def test_filter_by_tag(self):
        results = self.registry.filter_by_tag("review")
        self.assertGreater(len(results), 0)

    def test_filter_by_agent(self):
        results = self.registry.filter_by_agent("dev")
        self.assertIsInstance(results, list)

    def test_stats(self):
        s = self.registry.stats()
        self.assertGreater(s["total_contracts"], 0)

    def test_validate_with_retry(self):
        result, feedback = self.registry.validate_with_retry("code-review", {}, direction="input")
        self.assertFalse(result.valid)
        self.assertIsInstance(feedback, str)

    def test_register_custom(self):
        custom = dc.DeliveryContract(name="my-custom", description="Custom contract")
        self.registry.register(custom)
        self.assertIsNotNone(self.registry.get("my-custom"))


class TestMCPInterface(unittest.TestCase):
    def test_mcp_list(self):
        result = dc.mcp_list_contracts()
        self.assertIn("contracts", result)
        self.assertGreater(result["total"], 0)

    def test_mcp_validate(self):
        result = dc.mcp_validate_contract(
            contract_name="code-review",
            payload="{}",
            direction="input",
        )
        self.assertIn("valid", result)

    def test_mcp_validate_nonexistent(self):
        result = dc.mcp_validate_contract(
            contract_name="nonexistent",
            payload="{}",
        )
        self.assertFalse(result["valid"])


class TestCLIIntegration(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOL)] + list(args),
            capture_output=True, text=True, timeout=15,
        )

    def test_no_command_shows_help(self):
        r = self._run()
        self.assertIn(r.returncode, (0, 1))

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("delivery-contracts", r.stdout)

    def test_list(self):
        r = self._run("--project-root", "/tmp/test-dc", "list")
        self.assertEqual(r.returncode, 0)

    def test_stats(self):
        r = self._run("--project-root", "/tmp/test-dc", "stats")
        self.assertEqual(r.returncode, 0)

    def test_inspect(self):
        r = self._run("inspect", "--contract", "code-review")
        self.assertEqual(r.returncode, 0)

    def test_generate(self):
        r = self._run("generate", "--name", "test-contract")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
