"""Neo4j runtime graph projection for Grimoire memory records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from grimoire.codegraph.schemas import CodeEdge, CodeNode
from grimoire.evidence.schemas import EvidencePack, VerificationVerdict
from grimoire.memory.backends.base import MemoryEntry
from grimoire.memory.sidecar import DiaryRecord, KnowledgeFact
from grimoire.missions.schemas import Incident, LedgerEvent, Mission, MissionTask

_COUNTABLE_NODE_LABELS = frozenset({
    "GrimoireMemory",
    "GrimoireFact",
    "GrimoireDiary",
    "CodeNode",
    "GrimoireMission",
    "GrimoireTask",
    "GrimoireLedgerEvent",
    "GrimoireIncident",
    "GrimoireEvidencePack",
    "GrimoireVerificationVerdict",
    "GrimoireDecision",
    "WeaviateObject",
})
_COUNTABLE_RELATIONSHIPS = frozenset({
    "TAGGED_WITH",
    "CODE_EDGE",
    "HAS_TASK",
    "DEPENDS_ON",
    "CLAIMED_BY",
    "HAS_EVENT",
    "HAS_INCIDENT",
    "HAS_EVIDENCE",
    "HAS_VERDICT",
    "HAS_DECISION",
    "PRODUCED_DECISION",
    "TOUCHES_CODE",
    "COVERS_CODE",
    "VECTORIZED_AS",
    "VECTOR_FOR",
    "MEMORY_FOR",
})
_BATCH_SIZE = 1000


def _batches[T](items: list[T], size: int = _BATCH_SIZE) -> list[list[T]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _require_neo4j() -> Any:
    """Import the Neo4j driver, raising a clear optional-dependency error."""
    try:
        from neo4j import GraphDatabase

        return GraphDatabase
    except ImportError:
        raise ImportError("neo4j is not installed. Run:\n  pip install grimoire-kit[neo4j]") from None


@dataclass(frozen=True, slots=True)
class Neo4jGraphStatus:
    """Health and counts for the Neo4j memory graph projection."""

    healthy: bool
    detail: dict[str, Any]


class Neo4jMemoryGraph:
    """Write-through Neo4j projection for memory, facts, and diary records."""

    def __init__(self, *, uri: str, user: str, password: str, database: str = "neo4j") -> None:
        if not uri:
            raise ValueError("neo4j uri is required")
        if not password:
            raise ValueError("neo4j password is required")
        graph_database = _require_neo4j()
        self._uri = uri
        self._user = user
        self._database = database or "neo4j"
        self._driver = graph_database.driver(uri, auth=(user, password))
        self.ensure_schema()

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def database(self) -> str:
        return self._database

    def close(self) -> None:
        self._driver.close()

    def ensure_schema(self) -> None:
        """Create idempotent constraints used by runtime projections."""
        statements = (
            "CREATE CONSTRAINT grimoire_memory_id IF NOT EXISTS "
            "FOR (m:GrimoireMemory) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_tag_name IF NOT EXISTS "
            "FOR (t:GrimoireTag) REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT grimoire_fact_id IF NOT EXISTS "
            "FOR (f:GrimoireFact) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_entity_name IF NOT EXISTS "
            "FOR (e:GrimoireEntity) REQUIRE e.name IS UNIQUE",
            "CREATE CONSTRAINT grimoire_diary_id IF NOT EXISTS "
            "FOR (d:GrimoireDiary) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT code_node_id IF NOT EXISTS "
            "FOR (n:CodeNode) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_mission_id IF NOT EXISTS "
            "FOR (m:GrimoireMission) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_task_id IF NOT EXISTS "
            "FOR (t:GrimoireTask) REQUIRE t.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_ledger_event_id IF NOT EXISTS "
            "FOR (e:GrimoireLedgerEvent) REQUIRE e.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_incident_id IF NOT EXISTS "
            "FOR (i:GrimoireIncident) REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_evidence_pack_id IF NOT EXISTS "
            "FOR (p:GrimoireEvidencePack) REQUIRE p.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_verdict_id IF NOT EXISTS "
            "FOR (v:GrimoireVerificationVerdict) REQUIRE v.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_decision_id IF NOT EXISTS "
            "FOR (d:GrimoireDecision) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT grimoire_agent_name IF NOT EXISTS "
            "FOR (a:GrimoireAgent) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT weaviate_object_id IF NOT EXISTS "
            "FOR (w:WeaviateObject) REQUIRE w.id IS UNIQUE",
            "MATCH (m:GrimoireMemory) WHERE m.deleted IS NULL SET m.deleted = false",
        )
        for statement in statements:
            self._execute(statement)

    def upsert_memory(self, entry: MemoryEntry) -> None:
        """Create or update one memory node and its tag edges."""
        metadata = dict(entry.metadata)
        weaviate_id = str(metadata.get("weaviate_id", ""))
        weaviate_collection = str(metadata.get("weaviate_collection", ""))
        vector_backend = str(metadata.get("vector_backend", ""))
        payload = {
            "id": entry.id,
            "text": entry.text,
            "user_id": entry.user_id,
            "tags": list(entry.tags),
            "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
            "source": entry.source,
            "provenance_json": json.dumps(entry.provenance, ensure_ascii=False, sort_keys=True),
            "freshness": entry.freshness,
            "task_ref": entry.task_ref,
            "weaviate_id": weaviate_id,
            "weaviate_collection": weaviate_collection,
            "vector_backend": vector_backend,
        }
        self._execute(
            """
            MERGE (m:GrimoireMemory {id: $id})
            SET m.text = $text,
                m.user_id = $user_id,
                m.tags = $tags,
                m.metadata_json = $metadata_json,
                m.created_at = $created_at,
                m.updated_at = $updated_at,
                m.source = $source,
                m.provenance_json = $provenance_json,
                m.freshness = $freshness,
                m.task_ref = $task_ref,
                m.weaviate_id = $weaviate_id,
                m.weaviate_collection = $weaviate_collection,
                m.vector_backend = $vector_backend,
                m.deleted = false
            WITH m
            OPTIONAL MATCH (m)-[old:TAGGED_WITH]->(old_tag:GrimoireTag)
            WHERE NOT old_tag.name IN $tags
            DELETE old
            """,
            payload,
        )
        if entry.tags:
            self._execute(
                """
                MATCH (m:GrimoireMemory {id: $id})
                UNWIND $tags AS tag_name
                MERGE (t:GrimoireTag {name: tag_name})
                MERGE (m)-[:TAGGED_WITH]->(t)
                """,
                {"id": entry.id, "tags": list(entry.tags)},
            )
        self._execute(
            """
            MATCH (m:GrimoireMemory {id: $id})-[old:MEMORY_FOR]->()
            DELETE old
            """,
            {"id": entry.id},
        )
        self._link_projected_memory(entry.id, metadata)
        if weaviate_id:
            self._execute(
                """
                MATCH (m:GrimoireMemory {id: $id})
                MERGE (w:WeaviateObject {id: $weaviate_id})
                SET w.collection = $weaviate_collection,
                    w.backend = $vector_backend,
                    w.source_id = $id,
                    w.updated_at = datetime()
                MERGE (m)-[:VECTORIZED_AS]->(w)
                MERGE (w)-[:VECTOR_FOR]->(m)
                """,
                {
                    "id": entry.id,
                    "weaviate_id": weaviate_id,
                    "weaviate_collection": weaviate_collection,
                    "vector_backend": vector_backend,
                },
            )

    def _link_projected_memory(self, memory_id: str, metadata: dict[str, Any]) -> None:
        projection_kind = str(metadata.get("projection_kind", ""))
        code_projection_kinds = {"code_chunk", "code_symbol", "code_method", "code_test", "code_contract"}
        if projection_kind in code_projection_kinds:
            target_id = str(metadata.get("code_node_id") or metadata.get("projection_source_id") or "")
            if target_id:
                self._execute(
                    """
                    MATCH (m:GrimoireMemory {id: $memory_id})
                    MATCH (n:CodeNode {id: $target_id})
                    MERGE (m)-[r:MEMORY_FOR {kind: $kind}]->(n)
                    SET r.updated_at = datetime()
                    """,
                    {"memory_id": memory_id, "target_id": target_id, "kind": projection_kind},
                )
            return

        label_by_kind = {
            "mission": "GrimoireMission",
            "task": "GrimoireTask",
            "ledger_event": "GrimoireLedgerEvent",
            "incident": "GrimoireIncident",
            "evidence_pack": "GrimoireEvidencePack",
            "verdict": "GrimoireVerificationVerdict",
        }
        target_id = str(metadata.get("projection_source_id", ""))
        label = label_by_kind.get(projection_kind)
        if not target_id or label is None:
            return
        self._execute(
            f"""
            MATCH (m:GrimoireMemory {{id: $memory_id}})
            MATCH (n:{label} {{id: $target_id}})
            MERGE (m)-[r:MEMORY_FOR {{kind: $kind}}]->(n)
            SET r.updated_at = datetime()
            """,
            {"memory_id": memory_id, "target_id": target_id, "kind": projection_kind},
        )

    def delete_memory(self, entry_id: str) -> None:
        """Mark a memory as deleted while preserving provenance edges."""
        self._execute(
            """
            MATCH (m:GrimoireMemory {id: $id})
            SET m.deleted = true
            """,
            {"id": entry_id},
        )

    def upsert_fact(self, fact: KnowledgeFact) -> None:
        """Create or update a structured fact projection."""
        payload = fact.to_dict()
        payload["metadata_json"] = json.dumps({
            "wing": fact.wing,
            "hall": fact.hall,
            "room": fact.room,
        }, ensure_ascii=False, sort_keys=True)
        self._execute(
            """
            MERGE (f:GrimoireFact {id: $id})
            SET f.subject = $subject,
                f.predicate = $predicate,
                f.object = $object,
                f.valid_from = $valid_from,
                f.valid_to = $valid_to,
                f.confidence = $confidence,
                f.source_memory_id = $source_memory_id,
                f.metadata_json = $metadata_json,
                f.created_at = $created_at
            MERGE (s:GrimoireEntity {name: $subject})
            MERGE (o:GrimoireEntity {name: $object})
            MERGE (s)-[:FACT_SUBJECT]->(f)
            MERGE (f)-[:FACT_OBJECT]->(o)
            """,
            payload,
        )
        if fact.source_memory_id:
            self._execute(
                """
                MATCH (f:GrimoireFact {id: $id})
                MATCH (m:GrimoireMemory {id: $source_memory_id})
                MERGE (f)-[:SOURCED_FROM]->(m)
                """,
                {"id": fact.id, "source_memory_id": fact.source_memory_id},
            )

    def invalidate_fact(self, subject: str, predicate: str, object_: str, *, ended: str) -> None:
        """Reflect fact invalidation in Neo4j."""
        self._execute(
            """
            MATCH (f:GrimoireFact {
              subject: $subject,
              predicate: $predicate,
              object: $object
            })
            WHERE coalesce(f.valid_to, '') = ''
            SET f.valid_to = $ended
            """,
            {"subject": subject, "predicate": predicate, "object": object_, "ended": ended},
        )

    def upsert_diary(self, record: DiaryRecord) -> None:
        """Create or update one agent diary entry."""
        self._execute(
            """
            MERGE (d:GrimoireDiary {id: $id})
            SET d.agent_name = $agent_name,
                d.topic = $topic,
                d.entry = $entry,
                d.entry_format = $entry_format,
                d.related_memory_id = $related_memory_id,
                d.created_at = $created_at
            MERGE (a:GrimoireAgent {name: $agent_name})
            MERGE (a)-[:WROTE_DIARY]->(d)
            """,
            record.to_dict(),
        )
        if record.related_memory_id:
            self._execute(
                """
                MATCH (d:GrimoireDiary {id: $id})
                MATCH (m:GrimoireMemory {id: $related_memory_id})
                MERGE (d)-[:RELATED_TO]->(m)
                """,
                {"id": record.id, "related_memory_id": record.related_memory_id},
            )

    def upsert_code_nodes(self, nodes: list[CodeNode]) -> int:
        """Create or update code graph nodes."""
        for batch in _batches([node.to_dict() for node in nodes]):
            self._execute(
                """
                UNWIND $nodes AS row
                MERGE (n:CodeNode {id: row.id})
                SET n.kind = row.kind,
                    n.name = row.name,
                    n.file_path = row.file_path,
                    n.line_start = row.line_start,
                    n.line_end = row.line_end,
                    n.module = row.module,
                    n.docstring = row.docstring,
                    n.is_test = row.is_test,
                    n.is_public = row.is_public,
                    n.placeholder = false,
                    n.updated_at = datetime()
                """,
                {"nodes": batch},
            )
        return len(nodes)

    def upsert_code_edges(self, edges: list[CodeEdge]) -> int:
        """Create or update code graph edges."""
        for batch in _batches([edge.to_dict() for edge in edges]):
            self._execute(
                """
                UNWIND $edges AS row
                MERGE (a:CodeNode {id: row.from_node})
                ON CREATE SET
                    a.kind = 'import',
                    a.name = row.from_node,
                    a.file_path = '',
                    a.line_start = 0,
                    a.line_end = 0,
                    a.module = '',
                    a.docstring = '',
                    a.is_test = false,
                    a.is_public = true,
                    a.placeholder = true,
                    a.created_at = datetime()
                MERGE (b:CodeNode {id: row.to_node})
                ON CREATE SET
                    b.kind = 'import',
                    b.name = row.to_node,
                    b.file_path = '',
                    b.line_start = 0,
                    b.line_end = 0,
                    b.module = '',
                    b.docstring = '',
                    b.is_test = false,
                    b.is_public = true,
                    b.placeholder = true,
                    b.created_at = datetime()
                MERGE (a)-[r:CODE_EDGE {kind: row.kind}]->(b)
                SET r.updated_at = datetime()
                """,
                {"edges": batch},
            )
        return len(edges)

    def upsert_mission(self, mission: Mission) -> None:
        """Create or update one mission node."""
        payload = mission.to_dict()
        payload["scope_json"] = json.dumps(payload.pop("scope", {}), ensure_ascii=False, sort_keys=True)
        self._execute(
            """
            MERGE (m:GrimoireMission {id: $id})
            SET m.schema_version = $schema_version,
                m.title = $title,
                m.description = $description,
                m.status = $status,
                m.origin = $origin,
                m.risk_profile = $risk_profile,
                m.created_at = $created_at,
                m.created_by = $created_by,
                m.scope_json = $scope_json
            """,
            payload,
        )

    def upsert_task(self, task: MissionTask) -> None:
        """Create or update one task node and its mission/dependency edges."""
        payload = task.to_dict()
        dependencies = list(payload.pop("dependencies", []))
        claim = payload.pop("claim", None)
        payload["acceptance_json"] = json.dumps(payload.pop("acceptance", []), ensure_ascii=False, sort_keys=True)
        payload["guardrails_json"] = json.dumps(payload.pop("guardrails", []), ensure_ascii=False, sort_keys=True)
        payload["expected_evidence_json"] = json.dumps(
            payload.pop("expected_evidence", []),
            ensure_ascii=False,
            sort_keys=True,
        )
        payload["claim_json"] = json.dumps(claim or {}, ensure_ascii=False, sort_keys=True)
        self._execute(
            """
            MERGE (t:GrimoireTask {id: $id})
            SET t.schema_version = $schema_version,
                t.mission_id = $mission_id,
                t.title = $title,
                t.description = $description,
                t.status = $status,
                t.type = $type,
                t.risk_profile = $risk_profile,
                t.surface = $surface,
                t.owner = $owner,
                t.acceptance_json = $acceptance_json,
                t.guardrails_json = $guardrails_json,
                t.expected_evidence_json = $expected_evidence_json,
                t.claim_json = $claim_json,
                t.created_at = $created_at
            WITH t
            MATCH (m:GrimoireMission {id: $mission_id})
            MERGE (m)-[:HAS_TASK]->(t)
            """,
            payload,
        )
        self._execute(
            """
            MATCH (t:GrimoireTask {id: $id})-[old:DEPENDS_ON]->()
            DELETE old
            """,
            {"id": task.id},
        )
        for dependency in dependencies:
            self._execute(
                """
                MATCH (t:GrimoireTask {id: $task_id})
                MERGE (target:GrimoireTask {id: $target})
                MERGE (t)-[r:DEPENDS_ON {kind: $kind}]->(target)
                """,
                {"task_id": task.id, "target": dependency["target"], "kind": dependency["kind"]},
            )
        if claim:
            self._execute(
                """
                MATCH (t:GrimoireTask {id: $task_id})
                MERGE (a:GrimoireAgent {name: $actor_id})
                MERGE (t)-[:CLAIMED_BY]->(a)
                """,
                {"task_id": task.id, "actor_id": claim["actor_id"]},
            )

    def upsert_ledger_event(self, event: LedgerEvent) -> None:
        """Create or update one ledger event and link it to its entity."""
        payload = event.to_dict()
        payload["payload_json"] = json.dumps(payload.pop("payload", {}), ensure_ascii=False, sort_keys=True)
        self._execute(
            """
            MERGE (e:GrimoireLedgerEvent {id: $id})
            SET e.schema_version = $schema_version,
                e.event_type = $event_type,
                e.entity_id = $entity_id,
                e.entity_kind = $entity_kind,
                e.actor_id = $actor_id,
                e.created_at = $created_at,
                e.payload_json = $payload_json
            """,
            payload,
        )
        if event.entity_kind == "mission":
            self._execute(
                """
                MATCH (m:GrimoireMission {id: $entity_id})
                MATCH (e:GrimoireLedgerEvent {id: $event_id})
                MERGE (m)-[:HAS_EVENT]->(e)
                """,
                {"entity_id": event.entity_id, "event_id": event.id},
            )
        elif event.entity_kind == "task":
            self._execute(
                """
                MATCH (t:GrimoireTask {id: $entity_id})
                MATCH (e:GrimoireLedgerEvent {id: $event_id})
                MERGE (t)-[:HAS_EVENT]->(e)
                """,
                {"entity_id": event.entity_id, "event_id": event.id},
            )

    def upsert_incident(self, incident: Incident) -> None:
        """Create or update one task incident."""
        payload = incident.to_dict()
        payload["causes_json"] = json.dumps(payload.pop("causes", []), ensure_ascii=False, sort_keys=True)
        payload["recommended_actions_json"] = json.dumps(
            payload.pop("recommended_actions", []),
            ensure_ascii=False,
            sort_keys=True,
        )
        self._execute(
            """
            MERGE (i:GrimoireIncident {id: $id})
            SET i.schema_version = $schema_version,
                i.mission_id = $mission_id,
                i.task_id = $task_id,
                i.workflow_instance_id = $workflow_instance_id,
                i.kind = $kind,
                i.severity = $severity,
                i.status = $status,
                i.summary = $summary,
                i.causes_json = $causes_json,
                i.recommended_actions_json = $recommended_actions_json,
                i.created_at = $created_at
            WITH i
            MATCH (t:GrimoireTask {id: $task_id})
            MERGE (t)-[:HAS_INCIDENT]->(i)
            """,
            payload,
        )

    def upsert_evidence_pack(self, pack: EvidencePack) -> None:
        """Create or update one evidence pack and link it to its task."""
        payload = pack.to_dict()
        payload["items_json"] = json.dumps(payload.pop("items", []), ensure_ascii=False, sort_keys=True)
        payload["coverage_json"] = json.dumps(payload.pop("coverage", {}), ensure_ascii=False, sort_keys=True)
        self._execute(
            """
            MERGE (p:GrimoireEvidencePack {id: $id})
            SET p.schema_version = $schema_version,
                p.task_id = $task_id,
                p.workflow_instance_id = $workflow_instance_id,
                p.profile = $profile,
                p.items_json = $items_json,
                p.coverage_json = $coverage_json,
                p.created_at = $created_at
            WITH p
            MATCH (t:GrimoireTask {id: $task_id})
            MERGE (t)-[:HAS_EVIDENCE]->(p)
            """,
            payload,
        )

    def upsert_verdict(self, verdict: VerificationVerdict) -> None:
        """Create or update one verification verdict and link it to task/evidence."""
        payload = verdict.to_dict()
        payload["checks_json"] = json.dumps(payload.pop("checks", []), ensure_ascii=False, sort_keys=True)
        decision = payload.pop("decision", {})
        payload["decision_json"] = json.dumps(decision, ensure_ascii=False, sort_keys=True)
        payload["decision_id"] = f"decision:{verdict.id}"
        payload["decision_close_task"] = bool(decision.get("close_task", False))
        payload["decision_reopen_task"] = bool(decision.get("reopen_task", False))
        payload["decision_create_incident"] = bool(decision.get("create_incident", False))
        self._execute(
            """
            MERGE (v:GrimoireVerificationVerdict {id: $id})
            SET v.schema_version = $schema_version,
                v.task_id = $task_id,
                v.evidence_pack_id = $evidence_pack_id,
                v.verdict = $verdict,
                v.profile = $profile,
                v.checks_json = $checks_json,
                v.decision_json = $decision_json,
                v.created_by = $created_by,
                v.created_at = $created_at
            WITH v
            MATCH (t:GrimoireTask {id: $task_id})
            MERGE (t)-[:HAS_VERDICT]->(v)
            WITH v
            OPTIONAL MATCH (p:GrimoireEvidencePack {id: $evidence_pack_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (p)-[:HAS_VERDICT]->(v)
            )
            WITH v
            MATCH (t:GrimoireTask {id: $task_id})
            MERGE (d:GrimoireDecision {id: $decision_id})
            SET d.verdict_id = $id,
                d.task_id = $task_id,
                d.evidence_pack_id = $evidence_pack_id,
                d.close_task = $decision_close_task,
                d.reopen_task = $decision_reopen_task,
                d.create_incident = $decision_create_incident,
                d.decision_json = $decision_json,
                d.created_at = $created_at,
                d.updated_at = datetime()
            MERGE (v)-[:PRODUCED_DECISION]->(d)
            MERGE (t)-[:HAS_DECISION]->(d)
            """,
            payload,
        )

    def replace_task_code_references(self, task_ids: list[str], refs: list[dict[str, str]]) -> int:
        """Replace deterministic task-to-code reference edges."""
        if task_ids:
            self._execute(
                """
                MATCH (t:GrimoireTask)-[old:TOUCHES_CODE]->()
                WHERE t.id IN $task_ids
                DELETE old
                """,
                {"task_ids": task_ids},
            )
        unique_refs = _unique_reference_rows(refs, from_key="task_id")
        for batch in _batches(unique_refs):
            self._execute(
                """
                UNWIND $refs AS row
                MATCH (t:GrimoireTask {id: row.task_id})
                MATCH (n:CodeNode {id: row.code_node_id})
                MERGE (t)-[r:TOUCHES_CODE {source: row.source}]->(n)
                SET r.reason = row.reason,
                    r.updated_at = datetime()
                """,
                {"refs": batch},
            )
        return len(unique_refs)

    def replace_evidence_code_references(self, evidence_pack_ids: list[str], refs: list[dict[str, str]]) -> int:
        """Replace deterministic evidence-pack-to-code reference edges."""
        if evidence_pack_ids:
            self._execute(
                """
                MATCH (p:GrimoireEvidencePack)-[old:COVERS_CODE]->()
                WHERE p.id IN $evidence_pack_ids
                DELETE old
                """,
                {"evidence_pack_ids": evidence_pack_ids},
            )
        unique_refs = _unique_reference_rows(refs, from_key="evidence_pack_id")
        for batch in _batches(unique_refs):
            self._execute(
                """
                UNWIND $refs AS row
                MATCH (p:GrimoireEvidencePack {id: row.evidence_pack_id})
                MATCH (n:CodeNode {id: row.code_node_id})
                MERGE (p)-[r:COVERS_CODE {source: row.source}]->(n)
                SET r.reason = row.reason,
                    r.updated_at = datetime()
                """,
                {"refs": batch},
            )
        return len(unique_refs)

    def stats(self) -> dict[str, int]:
        """Return key graph counts."""
        return {
            "memories": self._count_memories(deleted=False),
            "deleted_memories": self._count_memories(deleted=True),
            "facts": self._count("GrimoireFact"),
            "diary_entries": self._count("GrimoireDiary"),
            "tag_edges": self._relationship_count("TAGGED_WITH"),
            "code_nodes": self._count("CodeNode"),
            "code_edges": self._relationship_count("CODE_EDGE"),
            "missions": self._count("GrimoireMission"),
            "tasks": self._count("GrimoireTask"),
            "ledger_events": self._count("GrimoireLedgerEvent"),
            "incidents": self._count("GrimoireIncident"),
            "evidence_packs": self._count("GrimoireEvidencePack"),
            "verdicts": self._count("GrimoireVerificationVerdict"),
            "decisions": self._count("GrimoireDecision"),
            "task_code_links": self._relationship_count("TOUCHES_CODE"),
            "evidence_code_links": self._relationship_count("COVERS_CODE"),
            "decision_edges": (
                self._relationship_count("HAS_DECISION")
                + self._relationship_count("PRODUCED_DECISION")
            ),
            "weaviate_objects": self._count("WeaviateObject"),
            "vectorized_edges": self._relationship_count("VECTORIZED_AS"),
            "memory_links": self._relationship_count("MEMORY_FOR"),
        }

    def health_check(self) -> Neo4jGraphStatus:
        """Verify connectivity and return graph projection stats."""
        try:
            self._driver.verify_connectivity()
            return Neo4jGraphStatus(
                healthy=True,
                detail={
                    "uri": self._uri,
                    "database": self._database,
                    **self.stats(),
                },
            )
        except Exception as exc:
            return Neo4jGraphStatus(
                healthy=False,
                detail={"uri": self._uri, "database": self._database, "error": str(exc)},
            )

    def _count(self, label: str) -> int:
        if label not in _COUNTABLE_NODE_LABELS:
            raise ValueError(f"Unsupported Neo4j count label: {label}")
        records, _, _ = self._driver.execute_query(
            f"MATCH (n:{label}) RETURN count(n) AS count",
            database_=self._database,
        )
        return int(records[0]["count"]) if records else 0

    def _count_memories(self, *, deleted: bool) -> int:
        records, _, _ = self._driver.execute_query(
            """
            MATCH (n:GrimoireMemory)
            WHERE coalesce(n.deleted, false) = $deleted
            RETURN count(n) AS count
            """,
            deleted=deleted,
            database_=self._database,
        )
        return int(records[0]["count"]) if records else 0

    def _relationship_count(self, relationship: str) -> int:
        if relationship not in _COUNTABLE_RELATIONSHIPS:
            raise ValueError(f"Unsupported Neo4j relationship count: {relationship}")
        records, _, _ = self._driver.execute_query(
            f"MATCH ()-[r:{relationship}]->() RETURN count(r) AS count",
            database_=self._database,
        )
        return int(records[0]["count"]) if records else 0

    def _execute(self, statement: str, params: dict[str, Any] | None = None) -> None:
        self._driver.execute_query(statement, **(params or {}), database_=self._database)


def _unique_reference_rows(refs: list[dict[str, str]], *, from_key: str) -> list[dict[str, str]]:
    """Deduplicate graph reference rows before Neo4j MERGE."""
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for ref in refs:
        source_id = str(ref.get(from_key, ""))
        code_node_id = str(ref.get("code_node_id", ""))
        source = str(ref.get("source", ""))
        if not source_id or not code_node_id:
            continue
        row = {
            from_key: source_id,
            "code_node_id": code_node_id,
            "source": source or "inferred",
            "reason": str(ref.get("reason", "")),
        }
        unique.setdefault((source_id, code_node_id, row["source"]), row)
    return list(unique.values())
