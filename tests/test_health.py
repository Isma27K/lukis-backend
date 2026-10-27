async def test_health_pings_database(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_reports_storage_down(client, storage):
    storage.down = True

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "File storage unavailable"}
