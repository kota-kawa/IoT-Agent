import json
import logging
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    DATABASE_URL,
    JOB_RESULT_TTL_SECONDS,
    MAX_COMPLETED_JOBS,
    REDIS_PREFIX,
    REDIS_URL,
    STORAGE_BACKEND,
)
from .models import DeviceState

logger = logging.getLogger("iot-agent.storage")

try:  # Optional dependencies (only required for Postgres/Redis backend)
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor, register_default_json, register_default_jsonb
    from psycopg2.pool import SimpleConnectionPool
except Exception:  # pragma: no cover - dependency optional
    psycopg2 = None
    Json = None
    RealDictCursor = None
    register_default_json = None
    register_default_jsonb = None
    SimpleConnectionPool = None

try:  # pragma: no cover - dependency optional
    import redis
except Exception:  # pragma: no cover - dependency optional
    redis = None


def _now_datetime() -> datetime:
    return datetime.now(timezone.utc)


def _ts_from_datetime(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return None


def _datetime_from_ts(value: Optional[float]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


class BaseStore:
    def __init__(self) -> None:
        self._initialized = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    def init(self) -> None:
        self._initialized = True

    def close(self) -> None:
        self._initialized = False

    def reset(self) -> None:
        raise NotImplementedError

    def list_devices(self) -> List[DeviceState]:
        raise NotImplementedError

    def get_device(self, device_id: str) -> Optional[DeviceState]:
        raise NotImplementedError

    def save_device(self, device: DeviceState) -> None:
        raise NotImplementedError

    def delete_device(self, device_id: str) -> bool:
        raise NotImplementedError

    def touch_device(self, device_id: str, last_seen: float) -> None:
        raise NotImplementedError

    def enqueue_job(self, device_id: str, command: Dict[str, Any], source: str) -> Optional[str]:
        raise NotImplementedError

    def update_job_metadata(self, job_id: str, fields: Dict[str, Any]) -> None:
        raise NotImplementedError

    def list_device_jobs(self, device_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def pop_next_job(self, device_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def cancel_job(self, job_id: str) -> Tuple[str, Optional[str]]:
        raise NotImplementedError

    def clear_device_jobs(self, device_id: str) -> int:
        raise NotImplementedError

    def record_job_result(
        self,
        device_id: str,
        job_id: Optional[str],
        result_record: Dict[str, Any],
        command: Optional[Dict[str, Any]] = None,
    ) -> None:
        raise NotImplementedError

    def pop_wait_result(self, device_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_completed_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def pending_job_device(self, job_id: str) -> Optional[str]:
        raise NotImplementedError

    def device_count(self) -> int:
        raise NotImplementedError


class InMemoryStore(BaseStore):
    def __init__(self) -> None:
        super().__init__()
        self.devices: Dict[str, DeviceState] = {}
        self.pending_jobs: Dict[str, str] = {}
        self.job_metadata: Dict[str, Dict[str, Any]] = {}
        self.completed_jobs: Dict[str, Dict[str, Any]] = {}
        self.completed_order: deque[str] = deque()

    def reset(self) -> None:
        self.devices.clear()
        self.pending_jobs.clear()
        self.job_metadata.clear()
        self.completed_jobs.clear()
        self.completed_order.clear()

    def list_devices(self) -> List[DeviceState]:
        devices = list(self.devices.values())
        for device in devices:
            device.queue_depth = len(device.job_queue)
        return devices

    def get_device(self, device_id: str) -> Optional[DeviceState]:
        device = self.devices.get(device_id)
        if device:
            device.queue_depth = len(device.job_queue)
        return device

    def save_device(self, device: DeviceState) -> None:
        self.devices[device.device_id] = device

    def delete_device(self, device_id: str) -> bool:
        return self.devices.pop(device_id, None) is not None

    def touch_device(self, device_id: str, last_seen: float) -> None:
        device = self.devices.get(device_id)
        if device:
            device.last_seen = last_seen

    def enqueue_job(self, device_id: str, command: Dict[str, Any], source: str) -> Optional[str]:
        device = self.devices.get(device_id)
        if not device:
            return None

        job_id = uuid.uuid4().hex
        device.job_queue.append({"job_id": job_id, "command": command})
        device.last_seen = time.time()
        self.pending_jobs[job_id] = device_id
        self.job_metadata[job_id] = {
            "job_id": job_id,
            "device_id": device_id,
            "command": dict(command),
            "queued_at": time.time(),
            "status": "pending",
            "source": source,
        }
        return job_id

    def update_job_metadata(self, job_id: str, fields: Dict[str, Any]) -> None:
        metadata = self.job_metadata.get(job_id)
        if metadata is None:
            return
        metadata.update(fields)

    def list_device_jobs(self, device_id: str) -> List[Dict[str, Any]]:
        jobs: List[Dict[str, Any]] = []
        for job_id, metadata in self.job_metadata.items():
            if metadata.get("device_id") != device_id:
                continue
            job_info = dict(metadata)
            job_info["job_id"] = job_id
            if job_id in self.pending_jobs:
                if job_info.get("status") not in {"dispatched", "cancelled"}:
                    job_info["status"] = "pending"
            elif job_id in self.completed_jobs and job_info.get("status") != "cancelled":
                job_info["status"] = "completed"
                job_info["result"] = self.completed_jobs[job_id]
            jobs.append({k: v for k, v in job_info.items() if v is not None})
        jobs.sort(key=lambda item: item.get("queued_at") or 0)
        return jobs

    def pop_next_job(self, device_id: str) -> Optional[Dict[str, Any]]:
        device = self.devices.get(device_id)
        if not device or not device.job_queue:
            return None
        job = device.job_queue.popleft()
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if isinstance(job_id, str):
            metadata = self.job_metadata.get(job_id)
            if metadata is not None and metadata.get("status") == "pending":
                metadata["status"] = "dispatched"
                metadata["dispatched_at"] = time.time()
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        metadata = self.job_metadata.get(job_id)
        pending_device = self.pending_jobs.get(job_id)
        result = self.completed_jobs.get(job_id)

        if not metadata and not pending_device and result is None:
            return None

        response_payload: Dict[str, Any] = {"job_id": job_id}

        if metadata:
            response_payload.update({k: v for k, v in metadata.items() if v is not None})

        if pending_device:
            response_payload["device_id"] = pending_device
            if metadata and metadata.get("status") == "dispatched":
                response_payload["status"] = "dispatched"
            else:
                response_payload["status"] = "pending"

        if result is not None:
            response_payload["status"] = "completed"
            response_payload["result"] = result
            response_payload.setdefault("device_id", result.get("device_id"))

        response_payload.setdefault("status", metadata.get("status") if metadata else "unknown")

        return response_payload

    def cancel_job(self, job_id: str) -> Tuple[str, Optional[str]]:
        if job_id in self.completed_jobs:
            metadata = self.job_metadata.get(job_id)
            device_id = metadata.get("device_id") if metadata else None
            return "completed", device_id

        device_id = self.pending_jobs.get(job_id)
        metadata = self.job_metadata.get(job_id)

        if not device_id:
            if metadata and metadata.get("status") == "cancelled":
                return "cancelled", metadata.get("device_id")
            return "not_found", None

        device = self.devices.get(device_id)
        if not device:
            self.pending_jobs.pop(job_id, None)
            if metadata is not None:
                metadata["status"] = "cancelled"
                metadata["cancelled_at"] = time.time()
            return "cancelled", device_id

        removed = False
        new_queue: deque[Dict[str, Any]] = deque()
        while device.job_queue:
            job = device.job_queue.popleft()
            if not removed and job.get("job_id") == job_id:
                removed = True
                continue
            new_queue.append(job)
        device.job_queue = new_queue

        if not removed:
            return "dispatched", device_id

        device.last_seen = time.time()
        self.pending_jobs.pop(job_id, None)
        if metadata is not None:
            metadata["status"] = "cancelled"
            metadata["cancelled_at"] = time.time()

        return "cancelled", device_id

    def clear_device_jobs(self, device_id: str) -> int:
        device = self.devices.get(device_id)
        if not device:
            return 0

        jobs_to_cancel = [
            job_id for job_id, assigned_dev in self.pending_jobs.items()
            if assigned_dev == device_id
        ]

        count = 0
        for job_id in jobs_to_cancel:
            self.pending_jobs.pop(job_id, None)
            metadata = self.job_metadata.get(job_id)
            if metadata is not None:
                metadata["status"] = "cancelled"
                metadata["cancelled_at"] = time.time()
            count += 1

        if device.job_queue:
            count += len(device.job_queue)
            device.job_queue.clear()

        device.last_seen = time.time()
        return count

    def record_job_result(
        self,
        device_id: str,
        job_id: Optional[str],
        result_record: Dict[str, Any],
        command: Optional[Dict[str, Any]] = None,
    ) -> None:
        device = self.devices.get(device_id)
        if not device:
            return

        device.last_seen = time.time()
        device.last_result = dict(result_record)

        if not job_id:
            return

        self.pending_jobs.pop(job_id, None)
        device.job_results[job_id] = dict(result_record)
        metadata = self.job_metadata.setdefault(job_id, {"job_id": job_id})
        metadata["device_id"] = device.device_id
        if command is not None:
            metadata.setdefault("command", command)
        metadata.setdefault("queued_at", time.time())
        metadata["status"] = "completed"
        metadata["completed_at"] = time.time()
        metadata["result_ok"] = bool(result_record.get("ok"))

        self.completed_jobs[job_id] = dict(result_record)
        try:
            self.completed_order.remove(job_id)
        except ValueError:
            pass
        self.completed_order.append(job_id)

        while len(self.completed_order) > MAX_COMPLETED_JOBS:
            oldest = self.completed_order.popleft()
            self.completed_jobs.pop(oldest, None)
            self.job_metadata.pop(oldest, None)

    def pop_wait_result(self, device_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        device = self.devices.get(device_id)
        if not device:
            return None
        return device.job_results.pop(job_id, None)

    def get_completed_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self.completed_jobs.get(job_id)

    def pending_job_device(self, job_id: str) -> Optional[str]:
        return self.pending_jobs.get(job_id)

    def device_count(self) -> int:
        return len(self.devices)


class PostgresRedisStore(BaseStore):
    def __init__(self, db_url: str, redis_url: str) -> None:
        super().__init__()
        self.db_url = db_url
        self.redis_url = redis_url
        self._pool: Optional[SimpleConnectionPool] = None
        self._redis = None
        self._prefix = REDIS_PREFIX

    def init(self) -> None:
        if self._initialized:
            return
        if psycopg2 is None or redis is None:
            raise RuntimeError("Postgres/Redis dependencies are not installed.")
        self._pool = SimpleConnectionPool(minconn=1, maxconn=5, dsn=self.db_url)
        self._redis = redis.Redis.from_url(self.redis_url, decode_responses=True)
        with self._get_conn() as conn:
            if register_default_jsonb:
                register_default_jsonb(conn, loads=json.loads)
            if register_default_json:
                register_default_json(conn, loads=json.loads)
        self._ensure_schema()
        self._initialized = True

    def close(self) -> None:
        if self._pool:
            self._pool.closeall()
        self._pool = None
        self._redis = None
        self._initialized = False

    @contextmanager
    def _get_conn(self):
        if not self._pool:
            raise RuntimeError("Database pool is not initialized.")
        conn = self._pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def _ensure_schema(self) -> None:
        schema_sql = [
            """
            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
                meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                last_seen TIMESTAMPTZ,
                registered_at TIMESTAMPTZ,
                approved BOOLEAN NOT NULL DEFAULT FALSE,
                last_result JSONB
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
                command JSONB NOT NULL,
                queued_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL,
                source TEXT,
                requested_via TEXT,
                wait_for_result BOOLEAN,
                dispatched_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                cancelled_at TIMESTAMPTZ,
                result_ok BOOLEAN
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS job_results (
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
                device_id TEXT NOT NULL,
                ok BOOLEAN,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_jobs_device_id ON jobs(device_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_job_results_device_id ON job_results(device_id)",
        ]
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for stmt in schema_sql:
                    cur.execute(stmt)

    def _queue_key(self, device_id: str) -> str:
        return f"{self._prefix}:device:{device_id}:queue"

    def _pending_key(self) -> str:
        return f"{self._prefix}:pending_jobs"

    def _result_key(self, job_id: str) -> str:
        return f"{self._prefix}:job_result:{job_id}"

    def reset(self) -> None:
        if not self._redis:
            return
        keys = self._redis.keys(f"{self._prefix}:*")
        if keys:
            self._redis.delete(*keys)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE job_results, jobs, devices RESTART IDENTITY CASCADE")

    def list_devices(self) -> List[DeviceState]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT device_id, capabilities, meta, last_seen, registered_at, approved, last_result
                    FROM devices
                    ORDER BY registered_at NULLS LAST, device_id
                    """
                )
                rows = cur.fetchall()

        devices: List[DeviceState] = []
        queue_depths: Dict[str, int] = {}
        if self._redis:
            pipeline = self._redis.pipeline()
            for row in rows:
                pipeline.llen(self._queue_key(row["device_id"]))
            results = pipeline.execute() if rows else []
            for row, depth in zip(rows, results):
                queue_depths[row["device_id"]] = int(depth or 0)

        for row in rows:
            device = DeviceState(
                device_id=row["device_id"],
                capabilities=row.get("capabilities") or [],
                meta=row.get("meta") or {},
                last_seen=_ts_from_datetime(row.get("last_seen")) or 0.0,
                registered_at=_ts_from_datetime(row.get("registered_at")) or 0.0,
                approved=bool(row.get("approved")),
                last_result=row.get("last_result"),
            )
            device.queue_depth = queue_depths.get(device.device_id, 0)
            devices.append(device)

        return devices

    def get_device(self, device_id: str) -> Optional[DeviceState]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT device_id, capabilities, meta, last_seen, registered_at, approved, last_result
                    FROM devices
                    WHERE device_id = %s
                    """,
                    (device_id,),
                )
                row = cur.fetchone()

        if not row:
            return None

        queue_depth = 0
        if self._redis:
            queue_depth = int(self._redis.llen(self._queue_key(device_id)) or 0)

        device = DeviceState(
            device_id=row["device_id"],
            capabilities=row.get("capabilities") or [],
            meta=row.get("meta") or {},
            last_seen=_ts_from_datetime(row.get("last_seen")) or 0.0,
            registered_at=_ts_from_datetime(row.get("registered_at")) or 0.0,
            approved=bool(row.get("approved")),
            last_result=row.get("last_result"),
        )
        device.queue_depth = queue_depth
        return device

    def save_device(self, device: DeviceState) -> None:
        if not self._pool:
            raise RuntimeError("Database not initialized.")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO devices (device_id, capabilities, meta, last_seen, registered_at, approved, last_result)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (device_id) DO UPDATE SET
                        capabilities = EXCLUDED.capabilities,
                        meta = EXCLUDED.meta,
                        last_seen = EXCLUDED.last_seen,
                        registered_at = EXCLUDED.registered_at,
                        approved = EXCLUDED.approved,
                        last_result = EXCLUDED.last_result
                    """,
                    (
                        device.device_id,
                        Json(device.capabilities or []) if Json else json.dumps(device.capabilities or []),
                        Json(device.meta or {}) if Json else json.dumps(device.meta or {}),
                        _datetime_from_ts(device.last_seen),
                        _datetime_from_ts(device.registered_at),
                        bool(device.approved),
                        (
                            Json(device.last_result) if Json else json.dumps(device.last_result)
                        )
                        if device.last_result is not None
                        else None,
                    ),
                )
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET last_seen = %s WHERE device_id = %s",
                    (now, device_id),
                )

    def delete_device(self, device_id: str) -> bool:
        deleted = False
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM devices WHERE device_id = %s", (device_id,))
                deleted = cur.rowcount > 0

        if self._redis:
            self._redis.delete(self._queue_key(device_id))
            # Remove pending jobs for this device
            pending_key = self._pending_key()
            to_remove = [
                job_id
                for job_id, value in self._redis.hscan_iter(pending_key)
                if value == device_id
            ]
            if to_remove:
                self._redis.hdel(pending_key, *to_remove)
        return deleted

    def touch_device(self, device_id: str, last_seen: float) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET last_seen = %s WHERE device_id = %s",
                    (_datetime_from_ts(last_seen), device_id),
                )

    def _device_exists(self, device_id: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM devices WHERE device_id = %s", (device_id,))
                return cur.fetchone() is not None

    def enqueue_job(self, device_id: str, command: Dict[str, Any], source: str) -> Optional[str]:
        if not self._device_exists(device_id):
            return None

        job_id = uuid.uuid4().hex
        now = _now_datetime()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (job_id, device_id, command, queued_at, status, source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job_id,
                        device_id,
                        Json(command) if Json else json.dumps(command),
                        now,
                        "pending",
                        source,
                    ),
                )

        if self._redis:
            self._redis.rpush(self._queue_key(device_id), job_id)
            self._redis.hset(self._pending_key(), job_id, device_id)

        return job_id

    def update_job_metadata(self, job_id: str, fields: Dict[str, Any]) -> None:
        if not fields:
            return
        columns = []
        values = []
        for key, value in fields.items():
            columns.append(f"{key} = %s")
            values.append(value)
        values.append(job_id)
        set_clause = ", ".join(columns)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE jobs SET {set_clause} WHERE job_id = %s",
                    tuple(values),
                )

    def list_device_jobs(self, device_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT j.job_id, j.device_id, j.command, j.queued_at, j.status, j.source,
                           j.requested_via, j.wait_for_result, j.dispatched_at, j.completed_at,
                           j.cancelled_at, j.result_ok, r.payload AS result
                    FROM jobs j
                    LEFT JOIN job_results r ON j.job_id = r.job_id
                    WHERE j.device_id = %s
                    ORDER BY j.queued_at NULLS LAST
                    """,
                    (device_id,),
                )
                rows = cur.fetchall()

        jobs: List[Dict[str, Any]] = []
        for row in rows:
            payload: Dict[str, Any] = {
                "job_id": row["job_id"],
                "device_id": row["device_id"],
                "command": row.get("command"),
                "queued_at": _ts_from_datetime(row.get("queued_at")),
                "status": row.get("status"),
                "source": row.get("source"),
                "requested_via": row.get("requested_via"),
                "wait_for_result": row.get("wait_for_result"),
                "dispatched_at": _ts_from_datetime(row.get("dispatched_at")),
                "completed_at": _ts_from_datetime(row.get("completed_at")),
                "cancelled_at": _ts_from_datetime(row.get("cancelled_at")),
                "result_ok": row.get("result_ok"),
            }
            if row.get("result") is not None:
                payload["result"] = row.get("result")
                payload["status"] = "completed"
            jobs.append({k: v for k, v in payload.items() if v is not None})
        return jobs

    def pop_next_job(self, device_id: str) -> Optional[Dict[str, Any]]:
        if not self._redis:
            return None
        job_id = self._redis.lpop(self._queue_key(device_id))
        if not job_id:
            return None

        now = _now_datetime()
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT command FROM jobs WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = %s, dispatched_at = %s
                    WHERE job_id = %s AND status = %s
                    """,
                    ("dispatched", now, job_id, "pending"),
                )

        command = row.get("command") if row else {}
        return {"job_id": job_id, "command": command}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT j.job_id, j.device_id, j.command, j.queued_at, j.status, j.source,
                           j.requested_via, j.wait_for_result, j.dispatched_at, j.completed_at,
                           j.cancelled_at, j.result_ok, r.payload AS result
                    FROM jobs j
                    LEFT JOIN job_results r ON j.job_id = r.job_id
                    WHERE j.job_id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()

        pending_device = self.pending_job_device(job_id)
        if not row and not pending_device:
            return None

        payload: Dict[str, Any] = {"job_id": job_id}
        if row:
            payload.update(
                {
                    "device_id": row.get("device_id"),
                    "command": row.get("command"),
                    "queued_at": _ts_from_datetime(row.get("queued_at")),
                    "status": row.get("status"),
                    "source": row.get("source"),
                    "requested_via": row.get("requested_via"),
                    "wait_for_result": row.get("wait_for_result"),
                    "dispatched_at": _ts_from_datetime(row.get("dispatched_at")),
                    "completed_at": _ts_from_datetime(row.get("completed_at")),
                    "cancelled_at": _ts_from_datetime(row.get("cancelled_at")),
                    "result_ok": row.get("result_ok"),
                }
            )
            if row.get("result") is not None:
                payload["result"] = row.get("result")
                payload["status"] = "completed"

        if pending_device and payload.get("status") in {None, "pending"}:
            payload["device_id"] = pending_device
            payload["status"] = payload.get("status") or "pending"

        return {k: v for k, v in payload.items() if v is not None}

    def cancel_job(self, job_id: str) -> Tuple[str, Optional[str]]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT device_id, status FROM jobs WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()

        if not row:
            return "not_found", None

        device_id = row.get("device_id")
        status = row.get("status")
        if status == "completed":
            return "completed", device_id
        if status == "cancelled":
            return "cancelled", device_id
        if status and status != "pending":
            return "dispatched", device_id

        removed = 0
        if self._redis:
            removed = int(self._redis.lrem(self._queue_key(device_id), 1, job_id) or 0)

        if removed == 0:
            return "dispatched", device_id

        now = _now_datetime()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs SET status = %s, cancelled_at = %s
                    WHERE job_id = %s
                    """,
                    ("cancelled", now, job_id),
                )

        if self._redis:
            self._redis.hdel(self._pending_key(), job_id)

        return "cancelled", device_id

    def clear_device_jobs(self, device_id: str) -> int:
        queue_depth = 0
        if self._redis:
            queue_key = self._queue_key(device_id)
            queue_depth = int(self._redis.llen(queue_key) or 0)
            self._redis.delete(queue_key)

        pending_ids: List[str] = []
        if self._redis:
            pending_key = self._pending_key()
            for job_id, value in self._redis.hscan_iter(pending_key):
                if value == device_id:
                    pending_ids.append(job_id)
            if pending_ids:
                self._redis.hdel(pending_key, *pending_ids)

        if pending_ids:
            now = _now_datetime()
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE jobs SET status = %s, cancelled_at = %s
                        WHERE job_id = ANY(%s) AND status != %s
                        """,
                        ("cancelled", now, pending_ids, "completed"),
                    )

        return queue_depth + len(pending_ids)

    def record_job_result(
        self,
        device_id: str,
        job_id: Optional[str],
        result_record: Dict[str, Any],
        command: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = _now_datetime()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE devices
                    SET last_seen = %s, last_result = %s
                    WHERE device_id = %s
                    """,
                    (
                        now,
                        Json(result_record) if Json else json.dumps(result_record),
                        device_id,
                    ),
                )

        if not job_id:
            return

        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT job_id FROM jobs WHERE job_id = %s",
                    (job_id,),
                )
                existing = cur.fetchone()

        if not existing:
            cmd_payload = command if command is not None else result_record.get("command") or {}
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO jobs (job_id, device_id, command, queued_at, status, source, completed_at, result_ok)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            job_id,
                            device_id,
                            Json(cmd_payload) if Json else json.dumps(cmd_payload),
                            now,
                            "completed",
                            "device",
                            now,
                            bool(result_record.get("ok")),
                        ),
                    )
        else:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE jobs
                        SET status = %s, completed_at = %s, result_ok = %s
                        WHERE job_id = %s
                        """,
                        ("completed", now, bool(result_record.get("ok")), job_id),
                    )

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO job_results (job_id, device_id, ok, payload, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        ok = EXCLUDED.ok,
                        payload = EXCLUDED.payload,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        job_id,
                        device_id,
                        bool(result_record.get("ok")),
                        Json(result_record) if Json else json.dumps(result_record),
                        now,
                    ),
                )

        if self._redis:
            self._redis.hdel(self._pending_key(), job_id)
            if JOB_RESULT_TTL_SECONDS and JOB_RESULT_TTL_SECONDS > 0:
                self._redis.set(
                    self._result_key(job_id),
                    json.dumps(result_record),
                    ex=JOB_RESULT_TTL_SECONDS,
                )
            else:
                self._redis.set(self._result_key(job_id), json.dumps(result_record))

    def pop_wait_result(self, device_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        if not self._redis:
            return None
        key = self._result_key(job_id)
        value = self._redis.get(key)
        if not value:
            return None
        self._redis.delete(key)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    def get_completed_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT payload FROM job_results WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        if row:
            return row.get("payload")
        return None

    def pending_job_device(self, job_id: str) -> Optional[str]:
        if not self._redis:
            return None
        value = self._redis.hget(self._pending_key(), job_id)
        return value if isinstance(value, str) else None

    def device_count(self) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM devices")
                row = cur.fetchone()
        return int(row[0]) if row else 0


_STORE: Optional[BaseStore] = None


def _create_store() -> BaseStore:
    backend = (STORAGE_BACKEND or "auto").lower()

    if backend == "memory":
        logger.info("Using in-memory storage backend.")
        return InMemoryStore()

    if backend in {"postgres", "postgres_redis", "auto"}:
        if DATABASE_URL and REDIS_URL:
            if psycopg2 is None or redis is None:
                raise RuntimeError("psycopg2/redis are required for Postgres+Redis backend.")
            logger.info("Using Postgres+Redis storage backend.")
            return PostgresRedisStore(DATABASE_URL, REDIS_URL)
        if backend != "auto":
            raise RuntimeError("DATABASE_URL and REDIS_URL must be set for Postgres+Redis backend.")

    logger.warning("DATABASE_URL/REDIS_URL not set; falling back to in-memory storage.")
    return InMemoryStore()


def get_store() -> BaseStore:
    global _STORE
    if _STORE is None:
        _STORE = _create_store()
    if not _STORE.initialized:
        _STORE.init()
    return _STORE


def reset_store() -> None:
    store = get_store()
    store.reset()
