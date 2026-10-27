import base64

from tests.helpers import JSON, add_member, personal_workspace_id, signup

PNG = b"\x89PNG\r\n\x1a\nfake image bytes"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def data_url(data: bytes, mime_type: str = "image/png") -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode()}"


async def new_drawing(client) -> str:
    workspace_id = await personal_workspace_id(client)
    response = await client.post(f"/workspaces/{workspace_id}/drawings", json={})
    return response.json()["id"]


async def upload(client, drawing_id: str, file_id: str, url: str):
    return await client.put(
        f"/drawings/{drawing_id}/files/{file_id}", json={"data_url": url}
    )


async def upload_thumbnail(client, drawing_id: str, light=PNG, dark=PNG):
    return await client.put(
        f"/drawings/{drawing_id}/thumbnail",
        json={"light": data_url(light), "dark": data_url(dark)},
    )


async def test_upload_and_download(client, storage):
    await signup(client)
    drawing_id = await new_drawing(client)

    response = await upload(client, drawing_id, "abc123", data_url(PNG))
    assert response.status_code == 204
    assert storage.objects[f"drawings/{drawing_id}/files/abc123"] == (
        PNG,
        "image/png",
    )

    download = await client.get(f"/drawings/{drawing_id}/files/abc123")
    assert download.status_code == 200
    assert download.content == PNG
    assert download.headers["content-type"] == "image/png"
    assert "immutable" in download.headers["cache-control"]


async def test_svgs_are_never_rendered_as_pages(client):
    await signup(client)
    drawing_id = await new_drawing(client)
    await upload(client, drawing_id, "svg", data_url(SVG, "image/svg+xml"))

    download = await client.get(f"/drawings/{drawing_id}/files/svg")

    assert download.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in download.headers["content-security-policy"]
    assert download.headers["content-disposition"] == "attachment"
    assert download.headers["x-content-type-options"] == "nosniff"


async def test_first_upload_of_a_file_id_wins(client):
    await signup(client)
    drawing_id = await new_drawing(client)

    await upload(client, drawing_id, "same", data_url(PNG))
    again = await upload(client, drawing_id, "same", data_url(b"other bytes"))

    assert again.status_code == 204
    assert (await client.get(f"/drawings/{drawing_id}/files/same")).content == PNG


async def test_rejects_bad_uploads(app, client):
    await signup(client)
    drawing_id = await new_drawing(client)
    app.state.settings.max_file_bytes = 8

    responses = {
        "html": await upload(client, drawing_id, "a", data_url(b"<p>", "text/html")),
        "not a data url": await upload(client, drawing_id, "a", "data:nope"),
        "bad base64": await upload(client, drawing_id, "a", "data:image/png;base64,!!"),
        "too large": await upload(client, drawing_id, "a", data_url(b"123456789")),
        "bad file id": await upload(client, drawing_id, "a.png", data_url(b"1")),
    }

    assert {name: r.status_code for name, r in responses.items()} == {
        "html": 422,
        "not a data url": 422,
        "bad base64": 422,
        "too large": 413,
        "bad file id": 422,
    }
    assert (await client.get(f"/drawings/{drawing_id}/files/a")).status_code == 404


async def test_storage_outage_is_a_503(client, storage):
    await signup(client)
    drawing_id = await new_drawing(client)
    storage.down = True

    response = await upload(client, drawing_id, "a", data_url(PNG))

    assert response.status_code == 503
    storage.down = False
    assert (await client.get(f"/drawings/{drawing_id}/files/a")).status_code == 404


async def test_viewers_download_but_dont_upload(app, make_client):
    owner, viewer, outsider = await make_client(), await make_client(), await make_client()
    await signup(owner)
    viewer_user = await signup(viewer, email="vic@example.com", name="Vic")
    await signup(outsider, email="eve@example.com", name="Eve")
    drawing_id = await new_drawing(owner)
    await add_member(app, await personal_workspace_id(owner), viewer_user["id"], "viewer")
    await upload(owner, drawing_id, "a", data_url(PNG))
    await upload_thumbnail(owner, drawing_id)

    assert (await viewer.get(f"/drawings/{drawing_id}/files/a")).status_code == 200
    assert (
        await viewer.get(f"/drawings/{drawing_id}/thumbnail/light")
    ).status_code == 200
    assert [
        (await upload(viewer, drawing_id, "b", data_url(PNG))).status_code,
        (await upload_thumbnail(viewer, drawing_id)).status_code,
        (
            await viewer.delete(f"/drawings/{drawing_id}/thumbnail", headers=JSON)
        ).status_code,
    ] == [403, 403, 403]

    assert [
        (await outsider.get(f"/drawings/{drawing_id}/files/a")).status_code,
        (await outsider.get(f"/drawings/{drawing_id}/thumbnail/light")).status_code,
        (await upload(outsider, drawing_id, "b", data_url(PNG))).status_code,
        (await upload_thumbnail(outsider, drawing_id)).status_code,
    ] == [404, 404, 404, 404]


async def test_thumbnails(client):
    await signup(client)
    workspace_id = await personal_workspace_id(client)
    drawing_id = await new_drawing(client)

    def listed():
        return client.get(f"/workspaces/{workspace_id}/drawings")

    assert (await listed()).json()[0]["thumbnail_version"] is None
    assert (
        await client.get(f"/drawings/{drawing_id}/thumbnail/light")
    ).status_code == 404

    response = await upload_thumbnail(client, drawing_id, PNG, b"dark " + PNG)
    assert response.status_code == 204
    version = (await listed()).json()[0]["thumbnail_version"]
    assert isinstance(version, int)
    light = await client.get(f"/drawings/{drawing_id}/thumbnail/light?v={version}")
    dark = await client.get(f"/drawings/{drawing_id}/thumbnail/dark")
    assert (light.content, dark.content) == (PNG, b"dark " + PNG)
    assert (
        await client.get(f"/drawings/{drawing_id}/thumbnail/sepia")
    ).status_code == 422

    svg = await client.put(
        f"/drawings/{drawing_id}/thumbnail",
        json={"light": data_url(SVG, "image/svg+xml"), "dark": data_url(PNG)},
    )
    assert svg.status_code == 422

    cleared = await client.delete(f"/drawings/{drawing_id}/thumbnail", headers=JSON)
    assert cleared.status_code == 204
    assert (await listed()).json()[0]["thumbnail_version"] is None
    assert (
        await client.get(f"/drawings/{drawing_id}/thumbnail/light")
    ).status_code == 404


async def test_duplicates_get_their_own_copy_of_files(client, storage):
    await signup(client)
    drawing_id = await new_drawing(client)
    await upload(client, drawing_id, "a", data_url(PNG))
    await upload_thumbnail(client, drawing_id)

    copy = (await client.post(f"/drawings/{drawing_id}/duplicate", json={})).json()

    assert copy["thumbnail_version"] is not None
    assert (await client.get(f"/drawings/{copy['id']}/files/a")).content == PNG
    assert (
        await client.get(f"/drawings/{copy['id']}/thumbnail/dark")
    ).content == PNG
    # Purging the original leaves the copy's files alone.
    await client.delete(f"/drawings/{drawing_id}", headers=JSON)
    await client.delete(f"/drawings/{drawing_id}/permanent", headers=JSON)
    assert not any(key.startswith(f"drawings/{drawing_id}/") for key in storage.objects)
    assert (await client.get(f"/drawings/{copy['id']}/files/a")).content == PNG
