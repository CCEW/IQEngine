"""Integration tests for the ADR-022 / ADR-023 AeroLake integration contract.

These cover the ADR-023 acceptance criteria against the versioned
/api/v1/integration surface, using a `local` datasource so that reconciliation
walks real SigMF pairs on disk instead of requiring MinIO or Azure.
"""

import asyncio
import json
import os

import pytest

DATATYPE = "ci16_le"  # 4 bytes per IQ sample
BYTES_PER_SAMPLE = 4


def sigmf_meta(frequency: int = 8486285000, operator: str = "cami") -> dict:
    return {
        "global": {
            "core:datatype": DATATYPE,
            "core:sample_rate": 1000000,
            "core:version": "1.0.0",
            "aerolake:signal_type": "IRIDIUM",
            "aerolake:operator": operator,
            "aerolake:modified": "2026-07-21T12:00:00+00:00",
        },
        "captures": [{"core:sample_start": 0, "core:frequency": frequency}],
        "annotations": [],
    }


@pytest.fixture
def local_datasource(tmp_path, monkeypatch, client):
    """A `local` datasource rooted at a temp dir, registered with the API."""
    monkeypatch.setenv("IQENGINE_BACKEND_LOCAL_FILEPATH", str(tmp_path))
    datasource = {
        "type": "api",
        "name": "aerolake",
        "account": "local",
        "container": "recordings",
        "description": "aerolake test bucket",
        "imageURL": None,
        "sasToken": None,
        "accountKey": None,
        "public": True,
        "readers": ["IQEngine-User"],
        "owners": ["IQEngine-Admin"],
    }
    response = client.post("/api/datasources", json=datasource)
    assert response.status_code in (201, 409), response.text
    return datasource, tmp_path


def write_pair(root, name, meta=None, data_bytes=4096, write_data=True, write_meta=True):
    if write_meta:
        (root / f"{name}.sigmf-meta").write_text(json.dumps(meta or sigmf_meta()))
    if write_data:
        (root / f"{name}.sigmf-data").write_bytes(b"\x00" * data_bytes)


def run_sync(client, account="local", container="recordings"):
    """Trigger a sync and return the terminal job document."""
    response = client.post(f"/api/v1/integration/datasources/{account}/{container}/sync")
    assert response.status_code == 202, response.text
    job = response.json()
    status = client.get(f"/api/v1/integration/sync/{job['job_id']}")
    assert status.status_code == 200, status.text
    return status.json()


# --- 1. valid new recording ------------------------------------------------


def test_valid_new_recording_is_indexed_and_searchable(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-a", data_bytes=4096)

    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["indexed"] == 1
    assert job["object_counts"]["invalid"] == 0
    assert job["error"] is None
    assert job["duration_seconds"] is not None
    assert job["started_at"] is not None and job["completed_at"] is not None

    search = client.get("/api/v1/integration/datasources/query?signal_type=IRIDIUM")
    assert search.status_code == 200, search.text
    body = search.json()
    assert body["state"] in ("current", "stale")
    assert body["last_successful_sync"] is not None
    assert [r["file_path"] for r in body["results"]] == ["rec-a"]


def test_sample_length_is_derived_from_data_object_size(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-len", data_bytes=8192)

    assert run_sync(client)["status"] == "completed"

    meta = client.get("/api/datasources/local/recordings/rec-len/meta")
    assert meta.status_code == 200, meta.text
    expected = 8192 / BYTES_PER_SAMPLE
    assert meta.json()["global"]["traceability:sample_length"] == expected


# --- 2. invalid metadata ---------------------------------------------------


def test_invalid_metadata_is_reported_and_not_indexed(client, local_datasource):
    _, root = local_datasource
    (root / "bad.sigmf-meta").write_text("{not valid json")
    (root / "bad.sigmf-data").write_bytes(b"\x00" * 1024)

    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["invalid"] == 1
    assert job["object_counts"]["indexed"] == 0
    codes = {e["code"] for e in job["error"]}
    assert "invalid_metadata" in codes
    assert any(e["path"] == "bad" for e in job["error"])

    search = client.get("/api/v1/integration/datasources/query")
    assert search.json()["results"] == []


def test_metadata_missing_datatype_is_rejected(client, local_datasource):
    _, root = local_datasource
    meta = sigmf_meta()
    del meta["global"]["core:datatype"]
    write_pair(root, "no-datatype", meta=meta)

    job = run_sync(client)
    assert job["object_counts"]["invalid"] == 1
    assert job["object_counts"]["indexed"] == 0
    detail = " ".join(e["detail"] for e in job["error"])
    assert "core:datatype" in detail


# --- 3. missing data/meta pair -------------------------------------------


def test_incomplete_pair_is_reported_and_not_active(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "orphan-meta", write_data=False)

    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["incomplete"] == 1
    assert job["object_counts"]["indexed"] == 0
    assert {e["code"] for e in job["error"]} == {"missing_data"}

    search = client.get("/api/v1/integration/datasources/query")
    assert search.json()["results"] == []


def test_orphan_data_object_is_not_indexed(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "orphan-data", write_meta=False)

    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["data"] == 1
    assert job["object_counts"]["indexed"] == 0
    assert job["object_counts"]["incomplete"] == 0


# --- 4. changed metadata -------------------------------------------------


def test_changed_metadata_is_detected_and_reflected(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-change", meta=sigmf_meta(operator="cami"))
    assert run_sync(client)["object_counts"]["indexed"] == 1

    # Republish the same path with different content and a new fingerprint.
    write_pair(root, "rec-change", meta=sigmf_meta(operator="rotated"), data_bytes=2048)
    os.utime(root / "rec-change.sigmf-meta", (0, 0))

    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["changed"] == 1
    assert job["object_counts"]["indexed"] == 1

    meta = client.get("/api/datasources/local/recordings/rec-change/meta")
    assert meta.json()["global"]["aerolake:operator"] == "rotated"

    # Still one catalog row, not a duplicate.
    search = client.get("/api/v1/integration/datasources/query?operator=rotated")
    assert len(search.json()["results"]) == 1


# --- 5. deleted object lifecycle: active -> missing -> deleted -----------


def test_deleted_object_becomes_missing_then_deleted_after_retention(client, local_datasource, monkeypatch):
    _, root = local_datasource
    write_pair(root, "rec-gone")
    assert run_sync(client)["object_counts"]["indexed"] == 1

    (root / "rec-gone.sigmf-meta").unlink()
    (root / "rec-gone.sigmf-data").unlink()

    # First reconciliation after deletion: soft-marked missing, not removed.
    monkeypatch.setenv("IQENGINE_SYNC_RETENTION_HOURS", "24")
    job = run_sync(client)
    assert job["status"] == "completed", job
    assert job["object_counts"]["missing"] == 1
    assert job["object_counts"]["deleted"] == 0

    # Non-destructive by default: the record still exists, just not active.
    assert client.get("/api/v1/integration/datasources/query").json()["results"] == []

    # Once retention has elapsed, the record transitions to deleted.
    monkeypatch.setenv("IQENGINE_SYNC_RETENTION_HOURS", "0")
    job = run_sync(client)
    assert job["object_counts"]["deleted"] == 1
    assert job["object_counts"]["missing"] == 0


def test_missing_object_returning_is_reactivated(client, local_datasource, monkeypatch):
    _, root = local_datasource
    write_pair(root, "rec-flap")
    assert run_sync(client)["object_counts"]["indexed"] == 1

    (root / "rec-flap.sigmf-meta").unlink()
    (root / "rec-flap.sigmf-data").unlink()
    monkeypatch.setenv("IQENGINE_SYNC_RETENTION_HOURS", "24")
    assert run_sync(client)["object_counts"]["missing"] == 1

    write_pair(root, "rec-flap")
    job = run_sync(client)
    assert job["object_counts"]["indexed"] == 1
    assert job["object_counts"]["missing"] == 0
    assert len(client.get("/api/v1/integration/datasources/query").json()["results"]) == 1


# --- 6. repeated sync (idempotence) -------------------------------------


def test_repeated_sync_is_idempotent(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-idem")

    first = run_sync(client)
    second = run_sync(client)
    third = run_sync(client)

    for job in (first, second, third):
        assert job["status"] == "completed", job
        assert job["object_counts"]["indexed"] == 1
        assert job["object_counts"]["invalid"] == 0

    # No duplicate catalog rows accumulate across repeated syncs.
    results = client.get("/api/v1/integration/datasources/query").json()["results"]
    assert len(results) == 1
    # Unchanged content must not be reported as changed.
    assert second["object_counts"]["changed"] == 0
    assert third["object_counts"]["changed"] == 0


# --- 7. concurrent sync requests ----------------------------------------


def test_concurrent_claims_share_one_job(client, local_datasource):
    """A claim made while another job is still active must reuse it rather than
    starting duplicate work. Driven through the app's own event loop via the
    TestClient portal so Motor stays on the loop it was created on."""
    from app.sync_jobs import claim_sync_job

    async def claim_twice():
        first = await claim_sync_job("local", "recordings")
        second = await claim_sync_job("local", "recordings")
        return first, second

    (first_id, first_created), (second_id, second_created) = client.portal.call(claim_twice)

    assert first_created is True
    assert second_created is False, "duplicate concurrent sync job was created"
    assert first_id == second_id


def test_parallel_claims_yield_a_single_job(client, local_datasource):
    from app.sync_jobs import claim_sync_job

    async def claim_many():
        return await asyncio.gather(*(claim_sync_job("local", "recordings") for _ in range(5)))

    claims = client.portal.call(claim_many)

    assert len({job_id for job_id, _ in claims}) == 1, "parallel claims produced multiple jobs"
    assert sum(1 for _, created in claims if created) == 1, "more than one claim won the race"


def test_sync_endpoint_reports_job_via_status_endpoint(client, local_datasource):
    # BackgroundTasks run to completion inside the TestClient request, so this
    # asserts the job-id round trip rather than in-flight sharing (covered by
    # the claim_sync_job tests above).
    first = client.post("/api/v1/integration/datasources/local/recordings/sync")
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    status = client.get(f"/api/v1/integration/sync/{job_id}")
    assert status.status_code == 200
    assert status.json()["job_id"] == job_id


# --- 8. expired / invalid credentials ------------------------------------


def test_expired_credentials_fail_safely_and_visibly(client, monkeypatch):
    """A datasource whose object store rejects credentials must fail the job,
    not silently report success or wipe the catalog."""
    datasource = {
        "type": "api",
        "name": "s3-bad-creds",
        "account": "us-east-1",
        "container": "aerolake-bucket",
        "description": "expired creds",
        "imageURL": None,
        "sasToken": None,
        "accountKey": None,
        "public": True,
        "readers": ["IQEngine-User"],
        "owners": ["IQEngine-Admin"],
        "awsAccessKeyId": "AKIAEXPIRED",
        "awsSecretAccessKey": "expired-secret",
        "s3EndpointUrl": "http://127.0.0.1:1",  # nothing listening
    }
    response = client.post("/api/datasources", json=datasource)
    assert response.status_code in (201, 409), response.text

    job = run_sync(client, account="us-east-1", container="aerolake-bucket")
    assert job["status"] == "failed", job
    assert job["error"], "failure must carry actionable error detail"
    assert job["completed_at"] is not None
    assert job["duration_seconds"] is not None


def test_sync_unknown_datasource_returns_404(client):
    response = client.post("/api/v1/integration/datasources/nope/nope/sync")
    assert response.status_code == 404


def test_unknown_job_id_returns_404(client):
    assert client.get("/api/v1/integration/sync/does-not-exist").status_code == 404


# --- 9. freshness / degraded state --------------------------------------


def test_state_is_unavailable_before_any_sync(client, local_datasource):
    body = client.get("/api/v1/integration/datasources/query").json()
    assert body["state"] == "unavailable"
    assert body["last_successful_sync"] is None


def test_state_is_current_after_successful_sync(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-fresh")
    assert run_sync(client)["status"] == "completed"

    body = client.get("/api/v1/integration/datasources/query").json()
    assert body["state"] == "current"
    assert body["last_successful_sync"] is not None


def test_state_is_stale_past_freshness_window(client, local_datasource, monkeypatch):
    _, root = local_datasource
    write_pair(root, "rec-stale")
    assert run_sync(client)["status"] == "completed"

    monkeypatch.setenv("IQENGINE_SYNC_FRESHNESS_HOURS", "3")
    body = client.get("/api/v1/integration/datasources/query").json()
    assert body["state"] == "current"

    monkeypatch.setenv("IQENGINE_SYNC_FRESHNESS_HOURS", "0")
    body = client.get("/api/v1/integration/datasources/query").json()
    assert body["state"] == "stale", body
    assert body["last_successful_sync"] is not None
    assert body["results"], "stale results are still returned, flagged as stale"


def test_state_is_sync_failed_after_failure(client, monkeypatch):
    datasource = {
        "type": "api",
        "name": "s3-fail",
        "account": "us-west-2",
        "container": "fail-bucket",
        "description": "fails",
        "imageURL": None,
        "sasToken": None,
        "accountKey": None,
        "public": True,
        "readers": ["IQEngine-User"],
        "owners": ["IQEngine-Admin"],
        "awsAccessKeyId": "AKIABAD",
        "awsSecretAccessKey": "bad",
        "s3EndpointUrl": "http://127.0.0.1:1",
    }
    assert client.post("/api/datasources", json=datasource).status_code in (201, 409)
    assert run_sync(client, "us-west-2", "fail-bucket")["status"] == "failed"

    body = client.get("/api/v1/integration/datasources/query").json()
    assert body["state"] == "sync failed", body


# --- ownership boundary / API contract ----------------------------------


def test_integration_datasource_lookup(client, local_datasource):
    response = client.get("/api/v1/integration/datasources/local/recordings")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account"] == "local"
    assert body["container"] == "recordings"


def test_integration_datasource_lookup_404(client):
    assert client.get("/api/v1/integration/datasources/x/y").status_code == 404


def test_integration_query_never_leaks_secrets(client, local_datasource):
    _, root = local_datasource
    write_pair(root, "rec-secret")
    run_sync(client)

    raw = client.get("/api/v1/integration/datasources/query").text
    for secret in ("accountKey", "sasToken", "awsSecretAccessKey"):
        assert secret not in raw


def test_only_active_records_are_searchable(client, local_datasource, monkeypatch):
    """Catalog search is restricted to catalog_status == active."""
    _, root = local_datasource
    write_pair(root, "rec-active")
    write_pair(root, "rec-removed")
    assert run_sync(client)["object_counts"]["indexed"] == 2

    (root / "rec-removed.sigmf-meta").unlink()
    (root / "rec-removed.sigmf-data").unlink()
    monkeypatch.setenv("IQENGINE_SYNC_RETENTION_HOURS", "24")
    run_sync(client)

    paths = [r["file_path"] for r in client.get("/api/v1/integration/datasources/query").json()["results"]]
    assert paths == ["rec-active"]
