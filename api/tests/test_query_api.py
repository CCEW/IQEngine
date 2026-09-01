import pytest
from tests.test_data import test_datasource, valid_metadata

from .test_data import valid_datasourcereference_array


@pytest.mark.asyncio
async def test_query_meta_success(client):
    query_condition = "min_frequency=8486280000&max_frequency=8486290000"

    client.post("/api/datasources", json=test_datasource).json()
    response = client.post(
        f'/api/datasources/{test_datasource["account"]}/{test_datasource["container"]}/file_path/meta',
        json=valid_metadata,
    )
    assert response.status_code == 201
    response = client.get(f"/api/datasources/query?{query_condition}")

    assert response.status_code == 200
    assert response.json() == valid_datasourcereference_array


@pytest.mark.asyncio
async def test_query_meta_aerolake_filters(client):
    client.post("/api/datasources", json=test_datasource)
    response = client.post(
        f'/api/datasources/{test_datasource["account"]}/{test_datasource["container"]}/file_path/meta',
        json=valid_metadata,
    )
    assert response.status_code == 201

    response = client.get(
        "/api/datasources/query?"
        "min_modified=2026-07-21T00:00:00Z&max_modified=2026-07-22T00:00:00Z"
        "&signal_type=IRIDIUM&hw=blade&operator=cami&recorder=ion"
    )

    assert response.status_code == 200
    assert response.json() == valid_datasourcereference_array


@pytest.mark.asyncio
async def test_query_meta_location_is_a_text_filter(client):
    """`location` matches global.aerolake:location as free text (e.g. "montreal"),
    case-insensitively and on substrings - it is not a geolocation filter."""
    client.post("/api/datasources", json=test_datasource)
    response = client.post(
        f'/api/datasources/{test_datasource["account"]}/{test_datasource["container"]}/file_path/meta',
        json=valid_metadata,
    )
    assert response.status_code == 201

    for query in ("location=montreal", "location=Montreal", "location=montr"):
        response = client.get(f"/api/datasources/query?{query}")
        assert response.status_code == 200, f"{query} -> {response.text}"
        assert response.json() == valid_datasourcereference_array, query


@pytest.mark.asyncio
async def test_query_meta_location_excludes_non_matches(client):
    client.post("/api/datasources", json=test_datasource)
    response = client.post(
        f'/api/datasources/{test_datasource["account"]}/{test_datasource["container"]}/file_path/meta',
        json=valid_metadata,
    )
    assert response.status_code == 201

    response = client.get("/api/datasources/query?location=toronto")
    assert response.status_code == 200
    assert response.json() == []
