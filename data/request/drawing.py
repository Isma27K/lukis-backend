from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]


class CreateDrawingRequest(BaseModel):
    title: Title = "Untitled"


class UpdateDrawingRequest(BaseModel):
    title: Title


class MoveDrawingRequest(BaseModel):
    workspace_id: str


class CreateVersionRequest(BaseModel):
    # Without a name it's a checkpoint that expires like automatic snapshots.
    name: Title | None = None


class RenameVersionRequest(BaseModel):
    name: Title


class SceneElement(BaseModel):
    """An Excalidraw element. Only the fields the merge relies on are checked;
    everything else is stored untouched."""

    model_config = ConfigDict(extra="allow")

    id: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    version: int
    versionNonce: int
    # Fractional index giving the element's z-order.
    index: str | None = None
    isDeleted: bool = False
    # Epoch milliseconds of the element's last change.
    updated: int = 0


class SaveSceneRequest(BaseModel):
    # Only elements changed since the last save need to be sent; the server
    # merges them into what it has.
    elements: list[SceneElement]
    app_state: dict[str, Any] | None = None


# Uploads are JSON like every other mutation (see middleware/csrf.py), with the
# bytes as a data URL, the form Excalidraw keeps files in anyway.
DataUrl = Annotated[str, StringConstraints(pattern=r"^data:", max_length=16_000_000)]


class UploadFileRequest(BaseModel):
    data_url: DataUrl


class SaveThumbnailRequest(BaseModel):
    # The same picture rendered for each editor theme.
    light: DataUrl
    dark: DataUrl
