"""Run, approval, and resource metadata repository."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from ids import new_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MetadataRepository(Protocol):
    def create_poc(
        self, poc_id: str, title: str, owner_user_id: str
    ) -> dict[str, Any]: ...
    def get_poc(self, poc_id: str) -> dict[str, Any]: ...
    def list_pocs(
        self, owner_user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...
    def search_pocs(
        self, owner_user_id: str, query: str, mode: str = "lexical", limit: int = 50
    ) -> list[dict[str, Any]]: ...
    def update_poc(self, poc_id: str, **changes: Any) -> dict[str, Any]: ...
    def set_current_version(self, poc_id: str, kind: str, version: str) -> None: ...
    def record_approval(
        self,
        poc_id: str,
        stage: str,
        version: str,
        approved_by: str,
        implicit: bool = False,
    ) -> dict[str, Any]: ...
    def check_gate(self, poc_id: str, stage: str, version: str) -> bool: ...
    def create_run(
        self,
        poc_id: str,
        stage: str,
        requested_by: str,
        inputs: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]: ...
    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]: ...
    def update_run_step(
        self, run_id: str, name: str, status: str, output_ref: str | None = None
    ) -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any]: ...
    def create_task(self, request: dict[str, Any]) -> dict[str, Any]: ...
    def finish_task(
        self,
        task_id: str,
        status: str,
        output_ref: str | None = None,
        error: dict[str, Any] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None: ...
    def append_message(self, poc_id: str, message: dict[str, Any]) -> None: ...
    def register_resource(self, resource: dict[str, Any]) -> None: ...
    def list_resources(
        self, poc_id: str, status: str = "active"
    ) -> list[dict[str, Any]]: ...
    def release_resource(self, resource_id: str) -> None: ...
    def release_resources(self, poc_id: str) -> int: ...
    def upsert_spec_embedding(self, document: dict[str, Any]) -> None: ...
    def put_deployment_secret(self, poc_id: str, payload: dict[str, str]) -> str: ...
    def get_deployment_secret(self, poc_id: str) -> dict[str, str]: ...
    def delete_deployment_secret(self, poc_id: str) -> None: ...


class InMemoryMetadataRepository:
    """Deterministic repository used locally and in unit tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.pocs: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.conversations: list[dict[str, Any]] = []
        self.resources: list[dict[str, Any]] = []
        self.spec_embeddings: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.deployment_secrets: dict[str, dict[str, str]] = {}
        self._task_sequences: dict[str, int] = {}

    def create_poc(self, poc_id: str, title: str, owner_user_id: str) -> dict[str, Any]:
        with self._lock:
            if poc_id in self.pocs:
                return deepcopy(self.pocs[poc_id])
            now = _now()
            self.pocs[poc_id] = {
                "poc_id": poc_id,
                "title": title,
                "owner_user_id": owner_user_id,
                "status": "drafting",
                "stack": {
                    "backend": "node-express",
                    "frontend": "react-vite",
                    "database": "mongodb",
                },
                "s3_prefix": f"pocs/{poc_id}/",
                "current_versions": {},
                "artifacts": {},
                "approvals": [],
                "created_at": now,
                "updated_at": now,
            }
            return deepcopy(self.pocs[poc_id])

    def get_poc(self, poc_id: str) -> dict[str, Any]:
        return deepcopy(self._poc(poc_id))

    def list_pocs(self, owner_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            pocs = [
                poc
                for poc in self.pocs.values()
                if poc["owner_user_id"] == owner_user_id
            ]
            pocs.sort(key=lambda poc: poc["updated_at"], reverse=True)
            return deepcopy(pocs[:limit])

    def search_pocs(
        self, owner_user_id: str, query: str, mode: str = "lexical", limit: int = 50
    ) -> list[dict[str, Any]]:
        del mode
        terms = [term.lower() for term in query.split() if term]
        if not terms:
            return self.list_pocs(owner_user_id, limit)
        return [
            poc
            for poc in self.list_pocs(owner_user_id, limit)
            if all(
                term in f"{poc['title']} {poc['status']} {poc['poc_id']}".lower()
                for term in terms
            )
        ][:limit]

    def update_poc(self, poc_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            self._poc(poc_id).update(changes, updated_at=_now())
            return self.get_poc(poc_id)

    def set_current_version(self, poc_id: str, kind: str, version: str) -> None:
        with self._lock:
            poc = self._poc(poc_id)
            poc["current_versions"][kind] = version
            poc["updated_at"] = _now()

    def record_approval(
        self,
        poc_id: str,
        stage: str,
        version: str,
        approved_by: str,
        implicit: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            poc = self._poc(poc_id)
            approval = {
                "stage": stage,
                "version": version,
                "approved_by": approved_by,
                "at": _now(),
                "implicit": implicit,
            }
            poc["approvals"] = [
                item
                for item in poc["approvals"]
                if not (item["stage"] == stage and item["version"] == version)
            ] + [approval]
            poc["updated_at"] = approval["at"]
            return deepcopy(approval)

    def check_gate(self, poc_id: str, stage: str, version: str) -> bool:
        return any(
            item["stage"] == stage and item["version"] == version
            for item in self._poc(poc_id)["approvals"]
        )

    def create_run(
        self,
        poc_id: str,
        stage: str,
        requested_by: str,
        inputs: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            duplicate = next(
                (
                    run
                    for run in self.runs.values()
                    if run["poc_id"] == poc_id
                    and run["stage"] == stage
                    and run["status"] in {"queued", "running"}
                ),
                None,
            )
            if duplicate:
                return deepcopy(duplicate)
            run_id = new_id("run")
            run = {
                "run_id": run_id,
                "poc_id": poc_id,
                "stage": stage,
                "status": "queued",
                "requested_by": requested_by,
                "started_by_agent": "chat_agent",
                "current_step": "queued",
                "steps": [],
                "inputs": inputs or {},
                "outputs": {},
                "repair_attempts": {},
                "trace_id": trace_id or new_id("task"),
                "started_at": None,
                "ended_at": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            self.runs[run_id] = run
            return deepcopy(run)

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            if run_id not in self.runs:
                raise KeyError(f"Unknown run: {run_id}")
            if changes.get("status") == "running" and not self.runs[run_id].get(
                "started_at"
            ):
                changes["started_at"] = _now()
            if changes.get("status") in {"succeeded", "failed", "cancelled"}:
                if not self.runs[run_id].get("started_at"):
                    changes["started_at"] = _now()
                changes["ended_at"] = _now()
            self.runs[run_id].update(changes)
            self.runs[run_id]["updated_at"] = _now()
            return deepcopy(self.runs[run_id])

    def update_run_step(
        self, run_id: str, name: str, status: str, output_ref: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            run = self._run(run_id)
            step = next((item for item in run["steps"] if item["name"] == name), None)
            if step is None:
                step = {"name": name, "started_at": _now()}
                run["steps"].append(step)
            step["status"] = status
            if output_ref:
                step["output_ref"] = output_ref
            if status in {"succeeded", "failed", "skipped"}:
                step["ended_at"] = _now()
            run.update(current_step=name, updated_at=_now())
            return deepcopy(step)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return deepcopy(self._run(run_id))

    def create_task(self, request: dict[str, Any]) -> dict[str, Any]:
        task_id = str(request["task_id"])
        with self._lock:
            run_id = str(request["run_id"])
            seq = self._task_sequences.get(run_id, 0) + 1
            self._task_sequences[run_id] = seq
            now = _now()
            task = {
                "task_id": task_id,
                "run_id": run_id,
                "poc_id": request["poc_id"],
                "seq": seq,
                "agent": request["agent"],
                "tool": request["tool"],
                "mode": request["mode"],
                "status": "running",
                "input_ref": request.get("params", {}).get("input_ref"),
                "trace_id": request.get("trace_id"),
                "duration_ms": None,
                "token_usage": {},
                "started_at": now,
                "ended_at": None,
                "created_at": now,
                "updated_at": now,
            }
            self.tasks[task_id] = task
            return deepcopy(task)

    def finish_task(
        self,
        task_id: str,
        status: str,
        output_ref: str | None = None,
        error: dict[str, Any] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        with self._lock:
            task = self.tasks[task_id]
            ended_at = _now()
            started_at = datetime.fromisoformat(task["started_at"])
            duration_ms = int(
                (datetime.fromisoformat(ended_at) - started_at).total_seconds() * 1000
            )
            self.tasks[task_id].update(
                status=status,
                output_ref=output_ref,
                error=error,
                duration_ms=duration_ms,
                token_usage=token_usage or {},
                ended_at=ended_at,
                updated_at=ended_at,
            )

    def append_message(self, poc_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            message_id = str(message.get("message_id") or new_id("task"))
            if any(
                item["poc_id"] == poc_id and item.get("message_id") == message_id
                for item in self.conversations
            ):
                return
            seq = 1 + sum(item["poc_id"] == poc_id for item in self.conversations)
            now = _now()
            self.conversations.append(
                {
                    "poc_id": poc_id,
                    "seq": seq,
                    **message,
                    "message_id": message_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )

    def register_resource(self, resource: dict[str, Any]) -> None:
        with self._lock:
            self.resources.append(
                {
                    **resource,
                    "status": "active",
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            )

    def list_resources(
        self, poc_id: str, status: str = "active"
    ) -> list[dict[str, Any]]:
        return [
            deepcopy(item)
            for item in self.resources
            if item["poc_id"] == poc_id and item["status"] == status
        ]

    def release_resource(self, resource_id: str) -> None:
        with self._lock:
            for resource in self.resources:
                if resource["resource_id"] == resource_id:
                    resource.update(
                        status="released", released_at=_now(), updated_at=_now()
                    )

    def release_resources(self, poc_id: str) -> int:
        released = 0
        with self._lock:
            for resource in self.resources:
                if resource["poc_id"] == poc_id and resource["status"] == "active":
                    resource["status"] = "released"
                    resource["released_at"] = _now()
                    resource["updated_at"] = _now()
                    released += 1
        return released

    def upsert_spec_embedding(self, document: dict[str, Any]) -> None:
        key = (
            str(document["poc_id"]),
            str(document["spec_version"]),
            int(document["chunk_id"]),
        )
        now = _now()
        with self._lock:
            existing = self.spec_embeddings.get(key, {})
            self.spec_embeddings[key] = {
                **existing,
                **document,
                "created_at": existing.get("created_at", now),
                "updated_at": now,
            }

    def put_deployment_secret(self, poc_id: str, payload: dict[str, str]) -> str:
        with self._lock:
            self.deployment_secrets[poc_id] = deepcopy(payload)
        return f"mongodb:deployment_secrets/{poc_id}"

    def get_deployment_secret(self, poc_id: str) -> dict[str, str]:
        with self._lock:
            if poc_id not in self.deployment_secrets:
                raise KeyError(f"Deployment secret not found: {poc_id}")
            return deepcopy(self.deployment_secrets[poc_id])

    def delete_deployment_secret(self, poc_id: str) -> None:
        with self._lock:
            self.deployment_secrets.pop(poc_id, None)

    def _poc(self, poc_id: str) -> dict[str, Any]:
        if poc_id not in self.pocs:
            raise KeyError(f"Unknown POC: {poc_id}")
        return self.pocs[poc_id]

    def _run(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.runs:
            raise KeyError(f"Unknown run: {run_id}")
        return self.runs[run_id]


class MongoMetadataRepository:
    """MongoDB implementation using the collection contracts from section 5.5."""

    def __init__(
        self,
        uri: str,
        database_name: str = "poc_builder",
        client: Any | None = None,
        search_index: str = "poc_search",
        poc_collection: str = "pocs",
    ) -> None:
        if not uri and client is None:
            raise ValueError("PLATFORM_MONGODB_URI is required")
        self.client: MongoClient[dict[str, Any]] = client or MongoClient(uri)
        self.search_index = search_index
        database = self.client[database_name]
        self.pocs = database[poc_collection]
        self.runs = database["runs"]
        self.tasks = database["tasks"]
        self.conversations = database["conversations"]
        self.resources = database["cloud_resources"]
        self.spec_embeddings = database["spec_embeddings"]
        self.deployment_secrets = database["deployment_secrets"]
        self.counters = database["sequence_counters"]
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        self.pocs.create_index("pov_id", unique=True)
        self.pocs.create_index(
            [("owner_user_id", ASCENDING), ("created_at", DESCENDING)]
        )
        self.runs.create_index("run_id", unique=True)
        self.runs.create_index([("poc_id", ASCENDING), ("started_at", DESCENDING)])
        self.runs.create_index("status")
        self.runs.create_index(
            [("poc_id", ASCENDING), ("stage", ASCENDING)],
            unique=True,
            partialFilterExpression={"status": {"$in": ["queued", "running"]}},
            name="one_active_stage_run",
        )
        self.tasks.create_index("task_id", unique=True)
        self.tasks.create_index(
            [("run_id", ASCENDING), ("seq", ASCENDING)], unique=True
        )
        self.conversations.create_index(
            [("poc_id", ASCENDING), ("seq", ASCENDING)], unique=True
        )
        self.conversations.create_index(
            [("poc_id", ASCENDING), ("message_id", ASCENDING)],
            unique=True,
            sparse=True,
        )
        self.resources.create_index(
            [("status", ASCENDING), ("ttl_expires_at", ASCENDING)]
        )
        self.resources.create_index("poc_id")
        self.spec_embeddings.create_index(
            [
                ("poc_id", ASCENDING),
                ("spec_version", ASCENDING),
                ("chunk_id", ASCENDING),
            ],
            unique=True,
        )
        self.spec_embeddings.create_index(
            [("owner_user_id", ASCENDING), ("poc_id", ASCENDING)]
        )
        self.deployment_secrets.create_index("poc_id", unique=True)

    @staticmethod
    def _clean(value: dict[str, Any] | None, label: str) -> dict[str, Any]:
        if value is None:
            raise KeyError(label)
        value.pop("_id", None)
        if "poc_id" not in value and "pov_id" in value:
            value["poc_id"] = value["pov_id"]
        return value

    def create_poc(self, poc_id: str, title: str, owner_user_id: str) -> dict[str, Any]:
        now = _now()
        document = {
            "pov_id": poc_id,
            "title": title,
            "owner_user_id": owner_user_id,
            "status": "drafting",
            "stack": {
                "backend": "node-express",
                "frontend": "react-vite",
                "database": "mongodb",
            },
            "s3_prefix": f"pocs/{poc_id}/",
            "current_versions": {},
            "artifacts": {},
            "approvals": [],
            "created_at": now,
            "updated_at": now,
        }
        self.pocs.update_one(
            {"pov_id": poc_id}, {"$setOnInsert": document}, upsert=True
        )
        return self.get_poc(poc_id)

    def get_poc(self, poc_id: str) -> dict[str, Any]:
        return self._clean(
            self.pocs.find_one({"pov_id": poc_id}), f"Unknown POC: {poc_id}"
        )

    def list_pocs(self, owner_user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            self._clean(item, "poc")
            for item in self.pocs.find({"owner_user_id": owner_user_id})
            .sort("updated_at", DESCENDING)
            .limit(limit)
        ]

    def search_pocs(
        self, owner_user_id: str, query: str, mode: str = "lexical", limit: int = 50
    ) -> list[dict[str, Any]]:
        terms = [term for term in query.split() if term]
        if not terms:
            return self.list_pocs(owner_user_id, limit)
        if mode == "full_text":
            try:
                return [
                    self._clean(item, "poc")
                    for item in self.pocs.aggregate(
                        [
                            {
                                "$search": {
                                    "index": self.search_index,
                                    "compound": {
                                        "filter": [
                                            {
                                                "equals": {
                                                    "path": "owner_user_id",
                                                    "value": owner_user_id,
                                                }
                                            }
                                        ],
                                        "must": [
                                            {
                                                "text": {
                                                    "query": query,
                                                    "path": [
                                                        "title",
                                                        "status",
                                                        "poc_id",
                                                    ],
                                                }
                                            }
                                        ],
                                    },
                                }
                            },
                            {"$limit": limit},
                        ]
                    )
                ]
            except (NotImplementedError, OperationFailure):
                pass
        filters = [{"$regex": term, "$options": "i"} for term in terms]
        return [
            self._clean(item, "poc")
            for item in self.pocs.find(
                {
                    "owner_user_id": owner_user_id,
                    "$and": [
                        {"$or": [{"title": term}, {"status": term}, {"poc_id": term}]}
                        for term in filters
                    ],
                }
            )
            .sort("updated_at", DESCENDING)
            .limit(limit)
        ]

    def update_poc(self, poc_id: str, **changes: Any) -> dict[str, Any]:
        setters = {key: value for key, value in changes.items()}
        setters["updated_at"] = _now()
        value = self.pocs.find_one_and_update(
            {"poc_id": poc_id}, {"$set": setters}, return_document=ReturnDocument.AFTER
        )
        return self._clean(value, f"Unknown POC: {poc_id}")

    def set_current_version(self, poc_id: str, kind: str, version: str) -> None:
        self.pocs.update_one(
            {"poc_id": poc_id},
            {"$set": {f"current_versions.{kind}": version, "updated_at": _now()}},
        )

    def record_approval(
        self,
        poc_id: str,
        stage: str,
        version: str,
        approved_by: str,
        implicit: bool = False,
    ) -> dict[str, Any]:
        approval = {
            "stage": stage,
            "version": version,
            "approved_by": approved_by,
            "at": _now(),
            "implicit": implicit,
        }
        self.pocs.update_one(
            {"poc_id": poc_id},
            {"$pull": {"approvals": {"stage": stage, "version": version}}},
        )
        self.pocs.update_one(
            {"poc_id": poc_id},
            {"$push": {"approvals": approval}, "$set": {"updated_at": _now()}},
        )
        return approval

    def check_gate(self, poc_id: str, stage: str, version: str) -> bool:
        return (
            self.pocs.count_documents(
                {
                    "poc_id": poc_id,
                    "approvals": {"$elemMatch": {"stage": stage, "version": version}},
                },
                limit=1,
            )
            == 1
        )

    def create_run(
        self,
        poc_id: str,
        stage: str,
        requested_by: str,
        inputs: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        query = {
            "poc_id": poc_id,
            "stage": stage,
            "status": {"$in": ["queued", "running"]},
        }
        existing = self.runs.find_one(query)
        if existing:
            return self._clean(existing, "active run")
        now = _now()
        document = {
            "run_id": new_id("run"),
            "poc_id": poc_id,
            "stage": stage,
            "status": "queued",
            "requested_by": requested_by,
            "started_by_agent": "chat_agent",
            "current_step": "queued",
            "steps": [],
            "inputs": inputs or {},
            "outputs": {},
            "repair_attempts": {},
            "trace_id": trace_id or new_id("task"),
            "started_at": None,
            "ended_at": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.runs.insert_one(document)
        except DuplicateKeyError:
            return self._clean(self.runs.find_one(query), "active run")
        return self._clean(document, "run")

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any]:
        current = self.get_run(run_id)
        if changes.get("status") == "running" and not current.get("started_at"):
            changes["started_at"] = _now()
        if changes.get("status") in {"succeeded", "failed", "cancelled"}:
            if not current.get("started_at"):
                changes["started_at"] = _now()
            changes["ended_at"] = _now()
        value = self.runs.find_one_and_update(
            {"run_id": run_id},
            {"$set": {**changes, "updated_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )
        return self._clean(value, f"Unknown run: {run_id}")

    def update_run_step(
        self, run_id: str, name: str, status: str, output_ref: str | None = None
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        steps = run.get("steps", [])
        step = next((item for item in steps if item["name"] == name), None)
        if step is None:
            step = {"name": name, "started_at": _now()}
            steps.append(step)
        step["status"] = status
        if output_ref:
            step["output_ref"] = output_ref
        if status in {"succeeded", "failed", "skipped"}:
            step["ended_at"] = _now()
        self.update_run(run_id, steps=steps, current_step=name)
        return step

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._clean(
            self.runs.find_one({"run_id": run_id}), f"Unknown run: {run_id}"
        )

    def create_task(self, request: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        run_id = str(request["run_id"])
        counter = self.counters.find_one_and_update(
            {"_id": f"task:{run_id}"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter is None:
            raise RuntimeError(f"Failed to allocate task sequence for {run_id}")
        document = {
            "task_id": request["task_id"],
            "run_id": run_id,
            "poc_id": request["poc_id"],
            "seq": int(counter["seq"]),
            "agent": request["agent"],
            "tool": request["tool"],
            "mode": request["mode"],
            "status": "running",
            "input_ref": request.get("params", {}).get("input_ref"),
            "trace_id": request.get("trace_id"),
            "duration_ms": None,
            "token_usage": {},
            "started_at": now,
            "ended_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self.tasks.update_one(
            {"task_id": document["task_id"]}, {"$setOnInsert": document}, upsert=True
        )
        return self._clean(
            self.tasks.find_one({"task_id": document["task_id"]}), "task"
        )

    def finish_task(
        self,
        task_id: str,
        status: str,
        output_ref: str | None = None,
        error: dict[str, Any] | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        task = self._clean(
            self.tasks.find_one({"task_id": task_id}), f"Unknown task: {task_id}"
        )
        ended_at = _now()
        started_at = datetime.fromisoformat(task["started_at"])
        duration_ms = int(
            (datetime.fromisoformat(ended_at) - started_at).total_seconds() * 1000
        )
        self.tasks.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "status": status,
                    "output_ref": output_ref,
                    "error": error,
                    "duration_ms": duration_ms,
                    "token_usage": token_usage or {},
                    "ended_at": ended_at,
                    "updated_at": ended_at,
                }
            },
        )

    def append_message(self, poc_id: str, message: dict[str, Any]) -> None:
        message_id = str(message.get("message_id") or new_id("task"))
        if self.conversations.count_documents(
            {"poc_id": poc_id, "message_id": message_id}, limit=1
        ):
            return
        counter = self.counters.find_one_and_update(
            {"_id": f"conversation:{poc_id}"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if counter is None:
            raise RuntimeError(f"Failed to allocate conversation sequence for {poc_id}")
        now = _now()
        document = {
            "poc_id": poc_id,
            "seq": int(counter["seq"]),
            **message,
            "message_id": message_id,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.conversations.insert_one(document)
        except DuplicateKeyError:
            return

    def register_resource(self, resource: dict[str, Any]) -> None:
        self.resources.update_one(
            {"resource_id": resource["resource_id"]},
            {
                "$setOnInsert": {
                    **resource,
                    "status": "active",
                    "created_at": _now(),
                    "updated_at": _now(),
                }
            },
            upsert=True,
        )

    def list_resources(
        self, poc_id: str, status: str = "active"
    ) -> list[dict[str, Any]]:
        return [
            self._clean(item, "resource")
            for item in self.resources.find({"poc_id": poc_id, "status": status})
        ]

    def release_resource(self, resource_id: str) -> None:
        self.resources.update_one(
            {"resource_id": resource_id},
            {
                "$set": {
                    "status": "released",
                    "released_at": _now(),
                    "updated_at": _now(),
                }
            },
        )

    def release_resources(self, poc_id: str) -> int:
        result = self.resources.update_many(
            {"poc_id": poc_id, "status": "active"},
            {
                "$set": {
                    "status": "released",
                    "released_at": _now(),
                    "updated_at": _now(),
                }
            },
        )
        return result.modified_count

    def upsert_spec_embedding(self, document: dict[str, Any]) -> None:
        query = {
            "poc_id": document["poc_id"],
            "spec_version": document["spec_version"],
            "chunk_id": document["chunk_id"],
        }
        self.spec_embeddings.update_one(
            query,
            {
                "$set": {**document, "updated_at": _now()},
                "$setOnInsert": {"created_at": _now()},
            },
            upsert=True,
        )

    def put_deployment_secret(self, poc_id: str, payload: dict[str, str]) -> str:
        now = _now()
        self.deployment_secrets.update_one(
            {"poc_id": poc_id},
            {
                "$set": {"payload": payload, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return f"mongodb:deployment_secrets/{poc_id}"

    def get_deployment_secret(self, poc_id: str) -> dict[str, str]:
        value = self.deployment_secrets.find_one({"poc_id": poc_id})
        if value is None:
            raise KeyError(f"Deployment secret not found: {poc_id}")
        return {str(key): str(item) for key, item in value.get("payload", {}).items()}

    def delete_deployment_secret(self, poc_id: str) -> None:
        self.deployment_secrets.delete_one({"poc_id": poc_id})
