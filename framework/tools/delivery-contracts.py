#!/usr/bin/env python3
"""
delivery-contracts.py — Delivery Contracts inter-agents Grimoire (BM-43 Story 4.4).
============================================================

Formalise les interfaces entre agents avec des contrats typés :
  - JSON Schema pour input/output de chaque type de tâche
  - Validation automatique avant envoi/réception
  - Retry avec feedback si la validation échoue (max 3)
  - Registry des contrats par type de tâche

Modes :
  validate  — Valide un payload contre un contrat
  list      — Liste les contrats disponibles
  generate  — Génère un template de contrat
  inspect   — Inspecte un contrat existant
  test      — Teste un contrat avec un payload d'exemple

Usage :
  python3 delivery-contracts.py validate --contract architecture-review \\
    --payload '{"files_to_review": ["src/auth.ts"], "constraints": "PCI", "story_id": "US-42"}'
  python3 delivery-contracts.py list
  python3 delivery-contracts.py generate --name code-review --output contracts/
  python3 delivery-contracts.py inspect --contract architecture-review

Stdlib only.

Références :
  - Grimoire Delivery Contract template: framework/delivery-contract.tpl.md
  - JSON Schema: https://json-schema.org/
  - Pydantic: https://docs.pydantic.dev/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

DELIVERY_CONTRACTS_VERSION = "1.0.0"

# ── Constants ────────────────────────────────────────────────────────────────

MAX_RETRIES = 3
CONTRACTS_DIR_NAME = "contracts"

# JSON Schema type mapping
PYTHON_TO_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "None": "null",
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class ContractField:
    """Un champ dans un schéma de contrat."""

    name: str
    field_type: str = "string"
    required: bool = False
    description: str = ""
    enum: list[str] = field(default_factory=list)
    min_length: int | None = None
    max_length: int | None = None
    default: str | None = None


@dataclass
class ContractSchema:
    """Schéma d'entrée ou sortie d'un contrat."""

    fields: list[ContractField] = field(default_factory=list)

    def to_json_schema(self) -> dict:
        """Convertit en JSON Schema standard."""
        properties = {}
        required = []
        for f in self.fields:
            prop: dict = {"type": f.field_type}
            if f.description:
                prop["description"] = f.description
            if f.enum:
                prop["enum"] = f.enum
            if f.min_length is not None:
                prop["minLength"] = f.min_length
            if f.max_length is not None:
                prop["maxLength"] = f.max_length
            properties[f.name] = prop
            if f.required:
                required.append(f.name)

        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    @classmethod
    def from_json_schema(cls, schema: dict) -> ContractSchema:
        """Parse un JSON Schema vers ContractSchema."""
        fields = []
        properties = schema.get("properties", {})
        required_fields = set(schema.get("required", []))
        for name, prop in properties.items():
            fields.append(ContractField(
                name=name,
                field_type=prop.get("type", "string"),
                required=name in required_fields,
                description=prop.get("description", ""),
                enum=prop.get("enum", []),
                min_length=prop.get("minLength"),
                max_length=prop.get("maxLength"),
            ))
        return cls(fields=fields)


@dataclass
class DeliveryContract:
    """Contrat de livraison entre agents."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    source_agent: str = ""
    target_agent: str = ""
    input_schema: ContractSchema = field(default_factory=ContractSchema)
    output_schema: ContractSchema = field(default_factory=ContractSchema)
    max_retries: int = MAX_RETRIES
    retry_feedback_template: str = "Validation échouée: {errors}. Veuillez corriger et renvoyer."
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Sérialise le contrat en dict."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "input_schema": self.input_schema.to_json_schema(),
            "output_schema": self.output_schema.to_json_schema(),
            "max_retries": self.max_retries,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DeliveryContract:
        """Parse un dict vers DeliveryContract."""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            source_agent=data.get("source_agent", ""),
            target_agent=data.get("target_agent", ""),
            input_schema=ContractSchema.from_json_schema(data.get("input_schema", {})),
            output_schema=ContractSchema.from_json_schema(data.get("output_schema", {})),
            max_retries=data.get("max_retries", MAX_RETRIES),
            tags=data.get("tags", []),
        )


@dataclass
class ValidationError:
    """Erreur de validation."""

    path: str
    message: str
    rule: str = ""


@dataclass
class ValidationResult:
    """Résultat de validation d'un payload contre un schéma."""

    valid: bool = True
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    retry_count: int = 0


# ── Schema Validator ─────────────────────────────────────────────────────────


class SchemaValidator:
    """
    Validateur JSON Schema léger (stdlib-only).

    Supporte: type, required, enum, minLength, maxLength, minimum, maximum,
    pattern, items (pour arrays), properties (pour objects).
    """

    def validate(self, payload: dict, schema: dict) -> ValidationResult:
        """Valide un payload contre un JSON Schema."""
        result = ValidationResult()
        self._validate_node(payload, schema, "", result)
        result.valid = len(result.errors) == 0
        return result

    def _validate_node(self, value, schema: dict, path: str, result: ValidationResult) -> None:
        expected_type = schema.get("type")
        if expected_type:
            if not self._check_type(value, expected_type):
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Type attendu '{expected_type}', reçu '{type(value).__name__}'",
                    rule="type",
                ))
                return

        # enum
        if "enum" in schema:
            if value not in schema["enum"]:
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Valeur '{value}' non dans enum {schema['enum']}",
                    rule="enum",
                ))

        # minLength / maxLength (strings)
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Longueur {len(value)} < minLength {schema['minLength']}",
                    rule="minLength",
                ))
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Longueur {len(value)} > maxLength {schema['maxLength']}",
                    rule="maxLength",
                ))

        # minimum / maximum (numbers)
        if isinstance(value, (int, float)):
            if "minimum" in schema and value < schema["minimum"]:
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Valeur {value} < minimum {schema['minimum']}",
                    rule="minimum",
                ))
            if "maximum" in schema and value > schema["maximum"]:
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Valeur {value} > maximum {schema['maximum']}",
                    rule="maximum",
                ))

        # pattern (strings)
        if isinstance(value, str) and "pattern" in schema:
            if not re.search(schema["pattern"], value):
                result.errors.append(ValidationError(
                    path=path or "$",
                    message=f"Valeur ne matche pas le pattern '{schema['pattern']}'",
                    rule="pattern",
                ))

        # properties + required (objects)
        if isinstance(value, dict) and "properties" in schema:
            required_fields = set(schema.get("required", []))
            for req in required_fields:
                if req not in value:
                    result.errors.append(ValidationError(
                        path=f"{path}.{req}" if path else req,
                        message=f"Champ requis '{req}' manquant",
                        rule="required",
                    ))
            for prop_name, prop_schema in schema["properties"].items():
                if prop_name in value:
                    child_path = f"{path}.{prop_name}" if path else prop_name
                    self._validate_node(value[prop_name], prop_schema, child_path, result)

        # items (arrays)
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                child_path = f"{path}[{i}]"
                self._validate_node(item, schema["items"], child_path, result)

    def _check_type(self, value, expected: str) -> bool:
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        expected_types = type_map.get(expected)
        if expected_types is None:
            return True  # Unknown type, accept
        # bool is subtype of int in Python — handle specially
        if expected == "integer" and isinstance(value, bool):
            return False
        if expected == "number" and isinstance(value, bool):
            return False
        return isinstance(value, expected_types)


# ── Built-in Contracts ──────────────────────────────────────────────────────

BUILTIN_CONTRACTS: dict[str, dict] = {
    "architecture-review": {
        "name": "architecture-review",
        "description": "Review d'architecture par l'architecte",
        "source_agent": "sm",
        "target_agent": "architect",
        "input_schema": {
            "type": "object",
            "required": ["files_to_review", "constraints", "story_id"],
            "properties": {
                "files_to_review": {"type": "array", "items": {"type": "string"}},
                "constraints": {"type": "string"},
                "story_id": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["decision", "rationale", "complexity"],
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "reject", "approve-with-conditions"]},
                "rationale": {"type": "string", "minLength": 50},
                "complexity": {"type": "string", "enum": ["S", "M", "L", "XL"]},
                "conditions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "tags": ["review", "architecture"],
    },
    "code-review": {
        "name": "code-review",
        "description": "Review de code par un pair",
        "source_agent": "sm",
        "target_agent": "dev",
        "input_schema": {
            "type": "object",
            "required": ["files", "story_id"],
            "properties": {
                "files": {"type": "array", "items": {"type": "string"}},
                "story_id": {"type": "string"},
                "focus_areas": {"type": "array", "items": {"type": "string"}},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["status", "issues"],
            "properties": {
                "status": {"type": "string", "enum": ["approved", "changes-requested", "blocked"]},
                "issues": {"type": "array", "items": {"type": "object"}},
                "summary": {"type": "string"},
            },
        },
        "tags": ["review", "code"],
    },
    "test-plan": {
        "name": "test-plan",
        "description": "Création de plan de test par QA",
        "source_agent": "sm",
        "target_agent": "qa",
        "input_schema": {
            "type": "object",
            "required": ["story_id", "acceptance_criteria"],
            "properties": {
                "story_id": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "tech_stack": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["test_cases", "coverage_estimate"],
            "properties": {
                "test_cases": {"type": "array", "items": {"type": "object"}},
                "coverage_estimate": {"type": "number", "minimum": 0, "maximum": 100},
                "risk_areas": {"type": "array", "items": {"type": "string"}},
            },
        },
        "tags": ["qa", "test"],
    },
    "story-implementation": {
        "name": "story-implementation",
        "description": "Implémentation d'une story par le dev",
        "source_agent": "sm",
        "target_agent": "dev",
        "input_schema": {
            "type": "object",
            "required": ["story_id", "description", "acceptance_criteria"],
            "properties": {
                "story_id": {"type": "string"},
                "description": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "tech_constraints": {"type": "string"},
                "files_to_modify": {"type": "array", "items": {"type": "string"}},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["status", "files_changed", "tests_written"],
            "properties": {
                "status": {"type": "string", "enum": ["complete", "partial", "blocked"]},
                "files_changed": {"type": "array", "items": {"type": "string"}},
                "tests_written": {"type": "integer", "minimum": 0},
                "notes": {"type": "string"},
            },
        },
        "tags": ["implementation", "dev"],
    },
    "documentation": {
        "name": "documentation",
        "description": "Rédaction de documentation technique",
        "source_agent": "sm",
        "target_agent": "tech-writer",
        "input_schema": {
            "type": "object",
            "required": ["topic", "audience"],
            "properties": {
                "topic": {"type": "string"},
                "audience": {"type": "string", "enum": ["developer", "user", "admin", "contributor"]},
                "source_files": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string", "enum": ["markdown", "api-doc", "guide"]},
            },
        },
        "output_schema": {
            "type": "object",
            "required": ["document_path", "sections"],
            "properties": {
                "document_path": {"type": "string"},
                "sections": {"type": "array", "items": {"type": "string"}},
                "word_count": {"type": "integer", "minimum": 0},
            },
        },
        "tags": ["documentation", "tech-writer"],
    },
}


# ── Contract Registry ────────────────────────────────────────────────────────


class ContractRegistry:
    """
    Registry des contrats disponibles.

    Charge les built-in contracts + les contrats du projet
    depuis {project_root}/contracts/*.json.
    """

    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root
        self._contracts: dict[str, DeliveryContract] = {}
        self._validator = SchemaValidator()
        self._load_builtins()
        if project_root:
            self._load_project_contracts()

    def _load_builtins(self) -> None:
        for name, data in BUILTIN_CONTRACTS.items():
            self._contracts[name] = DeliveryContract.from_dict(data)

    def _load_project_contracts(self) -> None:
        if not self.project_root:
            return
        contracts_dir = self.project_root / CONTRACTS_DIR_NAME
        if not contracts_dir.is_dir():
            return
        for f in sorted(contracts_dir.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                name = data.get("name", f.stem)
                self._contracts[name] = DeliveryContract.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue

    @property
    def contracts(self) -> dict[str, DeliveryContract]:
        return dict(self._contracts)

    def get(self, name: str) -> DeliveryContract | None:
        return self._contracts.get(name)

    def register(self, contract: DeliveryContract) -> None:
        self._contracts[contract.name] = contract

    def list_names(self) -> list[str]:
        return sorted(self._contracts.keys())

    def filter_by_tag(self, tag: str) -> list[DeliveryContract]:
        return [c for c in self._contracts.values() if tag in c.tags]

    def filter_by_agent(self, agent: str) -> list[DeliveryContract]:
        return [
            c for c in self._contracts.values()
            if c.source_agent == agent or c.target_agent == agent
        ]

    def validate_input(self, contract_name: str, payload: dict) -> ValidationResult:
        """Valide un payload d'entrée contre le schéma du contrat."""
        contract = self.get(contract_name)
        if not contract:
            return ValidationResult(
                valid=False,
                errors=[ValidationError(path="$", message=f"Contrat '{contract_name}' non trouvé")],
            )
        schema = contract.input_schema.to_json_schema()
        return self._validator.validate(payload, schema)

    def validate_output(self, contract_name: str, payload: dict) -> ValidationResult:
        """Valide un payload de sortie contre le schéma du contrat."""
        contract = self.get(contract_name)
        if not contract:
            return ValidationResult(
                valid=False,
                errors=[ValidationError(path="$", message=f"Contrat '{contract_name}' non trouvé")],
            )
        schema = contract.output_schema.to_json_schema()
        return self._validator.validate(payload, schema)

    def validate_with_retry(
        self,
        contract_name: str,
        payload: dict,
        direction: str = "input",
    ) -> tuple[ValidationResult, str]:
        """
        Valide avec feedback pour retry.

        Returns:
            (result, feedback_message)
        """
        if direction == "input":
            result = self.validate_input(contract_name, payload)
        else:
            result = self.validate_output(contract_name, payload)

        if result.valid:
            return result, ""

        contract = self.get(contract_name)
        if not contract:
            return result, "Contrat non trouvé"

        error_lines = [f"  - {e.path}: {e.message}" for e in result.errors]
        feedback = contract.retry_feedback_template.format(errors="\n".join(error_lines))
        return result, feedback

    def generate_template(self, name: str) -> dict:
        """Génère un template de contrat vide."""
        return {
            "name": name,
            "description": f"Contrat {name}",
            "version": "1.0.0",
            "source_agent": "",
            "target_agent": "",
            "input_schema": {
                "type": "object",
                "required": [],
                "properties": {},
            },
            "output_schema": {
                "type": "object",
                "required": [],
                "properties": {},
            },
            "max_retries": MAX_RETRIES,
            "tags": [],
        }

    def stats(self) -> dict:
        """Retourne des statistiques sur le registry."""
        all_tags = set()
        agents = set()
        for c in self._contracts.values():
            all_tags.update(c.tags)
            if c.source_agent:
                agents.add(c.source_agent)
            if c.target_agent:
                agents.add(c.target_agent)
        return {
            "total_contracts": len(self._contracts),
            "builtin": len(BUILTIN_CONTRACTS),
            "custom": len(self._contracts) - len([
                n for n in self._contracts if n in BUILTIN_CONTRACTS
            ]),
            "tags": sorted(all_tags),
            "agents": sorted(agents),
        }


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_validate_contract(
    contract_name: str,
    payload: str,
    direction: str = "input",
) -> dict:
    """
    MCP tool `bmad_validate_contract` — valide un payload contre un contrat.
    """
    try:
        payload_dict = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return {"valid": False, "error": "Invalid JSON payload"}

    registry = ContractRegistry()
    if direction == "input":
        result = registry.validate_input(contract_name, payload_dict)
    else:
        result = registry.validate_output(contract_name, payload_dict)

    return {
        "valid": result.valid,
        "errors": [asdict(e) for e in result.errors],
        "warnings": result.warnings,
    }


def mcp_list_contracts() -> dict:
    """
    MCP tool `bmad_list_contracts` — liste les contrats disponibles.
    """
    registry = ContractRegistry()
    contracts = []
    for name, contract in registry.contracts.items():
        contracts.append({
            "name": name,
            "description": contract.description,
            "source_agent": contract.source_agent,
            "target_agent": contract.target_agent,
            "tags": contract.tags,
        })
    return {"contracts": contracts, "total": len(contracts)}


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delivery Contracts — Contrats typés inter-agents Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=None,
                        help="Racine du projet pour charger les contrats custom")
    parser.add_argument("--version", action="version",
                        version=f"delivery-contracts {DELIVERY_CONTRACTS_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # validate
    val_p = sub.add_parser("validate", help="Valider un payload contre un contrat")
    val_p.add_argument("--contract", required=True, help="Nom du contrat")
    val_p.add_argument("--payload", required=True, help="Payload JSON à valider")
    val_p.add_argument("--direction", choices=["input", "output"], default="input",
                       help="Direction (input ou output)")
    val_p.add_argument("--json", action="store_true", help="Output JSON")

    # list
    list_p = sub.add_parser("list", help="Lister les contrats")
    list_p.add_argument("--tag", default="", help="Filtrer par tag")
    list_p.add_argument("--agent", default="", help="Filtrer par agent")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # generate
    gen_p = sub.add_parser("generate", help="Générer un template de contrat")
    gen_p.add_argument("--name", required=True, help="Nom du contrat")
    gen_p.add_argument("--output", type=Path, default=None, help="Répertoire de sortie")

    # inspect
    insp_p = sub.add_parser("inspect", help="Inspecter un contrat")
    insp_p.add_argument("--contract", required=True, help="Nom du contrat")
    insp_p.add_argument("--json", action="store_true", help="Output JSON")

    # stats
    sub.add_parser("stats", help="Statistiques du registry")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    registry = ContractRegistry(project_root=args.project_root)

    if args.command == "validate":
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError:
            print("  ❌ Payload JSON invalide", file=sys.stderr)
            sys.exit(1)

        if args.direction == "input":
            result = registry.validate_input(args.contract, payload)
        else:
            result = registry.validate_output(args.contract, payload)

        if getattr(args, "json", False):
            print(json.dumps({
                "valid": result.valid,
                "errors": [asdict(e) for e in result.errors],
            }, ensure_ascii=False, indent=2))
        else:
            icon = "✅" if result.valid else "❌"
            print(f"\n  {icon} Contrat '{args.contract}' — {args.direction}")
            if result.errors:
                print(f"  Erreurs ({len(result.errors)}) :")
                for e in result.errors:
                    print(f"    • {e.path}: {e.message}")
            else:
                print("  Validation réussie")
            print()

    elif args.command == "list":
        contracts = list(registry.contracts.values())
        if args.tag:
            contracts = [c for c in contracts if args.tag in c.tags]
        if args.agent:
            contracts = [c for c in contracts if c.source_agent == args.agent or c.target_agent == args.agent]

        if getattr(args, "json", False):
            out = [c.to_dict() for c in contracts]
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"\n  Contrats ({len(contracts)}) :")
            print(f"  {'─' * 60}")
            for c in contracts:
                tags_str = ", ".join(c.tags) if c.tags else "-"
                print(f"    {c.name:25s} │ {c.source_agent:>10s} → {c.target_agent:<10s} │ {tags_str}")
            print()

    elif args.command == "generate":
        template = registry.generate_template(args.name)
        output = json.dumps(template, ensure_ascii=False, indent=2)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            outpath = args.output / f"{args.name}.json"
            outpath.write_text(output + "\n", encoding="utf-8")
            print(f"\n  ✅ Template généré : {outpath}\n")
        else:
            print(output)

    elif args.command == "inspect":
        contract = registry.get(args.contract)
        if not contract:
            print(f"  ❌ Contrat '{args.contract}' non trouvé", file=sys.stderr)
            sys.exit(1)
        data = contract.to_dict()
        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"\n  📋 Contrat : {contract.name}")
            print(f"  {'─' * 50}")
            print(f"  Description : {contract.description}")
            print(f"  Version     : {contract.version}")
            print(f"  Source      : {contract.source_agent}")
            print(f"  Target      : {contract.target_agent}")
            print(f"  Max retries : {contract.max_retries}")
            print(f"  Tags        : {', '.join(contract.tags) if contract.tags else '-'}")

            input_s = contract.input_schema.to_json_schema()
            print("\n  Input Schema :")
            for prop_name in input_s.get("properties", {}):
                req = "✓" if prop_name in input_s.get("required", []) else " "
                ptype = input_s["properties"][prop_name].get("type", "?")
                print(f"    [{req}] {prop_name:25s} : {ptype}")

            output_s = contract.output_schema.to_json_schema()
            print("\n  Output Schema :")
            for prop_name in output_s.get("properties", {}):
                req = "✓" if prop_name in output_s.get("required", []) else " "
                ptype = output_s["properties"][prop_name].get("type", "?")
                print(f"    [{req}] {prop_name:25s} : {ptype}")
            print()

    elif args.command == "stats":
        s = registry.stats()
        print("\n  📊 Registry Stats")
        print(f"  {'─' * 40}")
        print(f"  Total contrats : {s['total_contracts']}")
        print(f"  Built-in       : {s['builtin']}")
        print(f"  Custom         : {s['custom']}")
        print(f"  Tags           : {', '.join(s['tags'])}")
        print(f"  Agents         : {', '.join(s['agents'])}")
        print()


if __name__ == "__main__":
    main()
