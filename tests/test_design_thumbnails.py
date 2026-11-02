"""PowerPoint thumbnails remain bounded and follow design-library access rules."""

import base64
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from artifact_studio import app as service
from artifact_studio.design_library import DesignCreate, DesignDelete, DesignLibrary
from artifact_studio.design_thumbnails import design_thumbnail
from artifact_studio.models import FileInput
from artifact_studio.storage import Store
from tests.design_factory import powerpoint_source


def package(thumbnail: bytes | None = None, name: str = "docProps/thumbnail.png") -> bytes:
    """Build a valid fictional PowerPoint with an optional embedded thumbnail."""
    output = io.BytesIO(powerpoint_source())
    if thumbnail is not None:
        with ZipFile(output, "a", ZIP_DEFLATED) as archive:
            archive.writestr(name, thumbnail)
    return output.getvalue()


def image_bytes(size: tuple[int, int] = (1200, 600)) -> bytes:
    """Build a small compressed raster image for thumbnail tests."""
    output = io.BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return output.getvalue()


def test_thumbnail_endpoint_and_access_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    library = DesignLibrary(store)
    item = library.create(
        DesignCreate(
            name="Presentation",
            files=[
                FileInput(
                    name="deck.pptx", data_base64=base64.b64encode(package(image_bytes())).decode()
                ),
                FileInput(name="brief.txt", data_base64=base64.b64encode(b"brief").decode()),
            ],
        )
    )
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    deck = next(file for file in item.files if file.name == "deck.pptx")
    brief = next(file for file in item.files if file.name == "brief.txt")
    url = f"/api/design-library/{item.id}/thumbnail/{deck.path}"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "no-store" in response.headers["cache-control"]
    assert "sandbox; default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (640, 320)
    assert client.get(f"/api/design-library/{item.id}/thumbnail/{brief.path}").status_code == 404
    assert client.get(f"/api/design-library/{item.id}/thumbnail/unknown.pptx").status_code == 404
    _ = Path(deck.local_path).write_bytes(package())
    assert client.get(url).status_code == 204
    item = library.get(item.id)
    library.delete(item.id, DesignDelete(expected_version=item.version))
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "data",
    [
        package(),
        b"not a zip",
        package(b"not an image"),
        package(image_bytes((5000, 4001))),
        package(b"x" * 10_000_001),
    ],
)
def test_invalid_or_oversized_thumbnail_is_unavailable(tmp_path: Path, data: bytes) -> None:
    source = tmp_path / "deck.pptx"
    _ = source.write_bytes(data)
    with pytest.raises(FileNotFoundError):
        design_thumbnail(source)


def test_oversized_archive_is_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "deck.pptx"
    with source.open("wb") as file:
        _ = file.seek(100_000_000)
        _ = file.write(b"x")
    with pytest.raises(FileNotFoundError):
        design_thumbnail(source)


def test_raster_previews_preserve_transparency_and_use_first_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transparent = io.BytesIO()
    Image.new("RGBA", (1280, 640), (20, 30, 40, 0)).save(transparent, format="PNG")
    animation = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(
        animation,
        format="GIF",
        save_all=True,
        append_images=[Image.new("RGB", (40, 20), "blue")],
    )
    store = Store(tmp_path / "collection", tmp_path / "state/review.sqlite3")
    item = DesignLibrary(store).create(
        DesignCreate(
            name="Images",
            files=[
                FileInput(
                    name="alpha.png", data_base64=base64.b64encode(transparent.getvalue()).decode()
                ),
                FileInput(
                    name="animated.gif", data_base64=base64.b64encode(animation.getvalue()).decode()
                ),
                FileInput(name="invalid.webp", data_base64=base64.b64encode(b"invalid").decode()),
            ],
        )
    )
    monkeypatch.setattr(service, "store", store)
    client = TestClient(service.app, base_url="http://127.0.0.1")
    files = {file.name: file for file in item.files}
    prefix = f"/api/design-library/{item.id}/thumbnail/"
    alpha = client.get(prefix + files["alpha.png"].path)
    assert alpha.status_code == 200
    with Image.open(io.BytesIO(alpha.content)) as image:
        assert image.size == (640, 320)
        assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    animated = client.get(prefix + files["animated.gif"].path)
    assert animated.status_code == 200
    with Image.open(io.BytesIO(animated.content)) as image:
        assert image.getpixel((0, 0)) == (255, 0, 0)
    assert client.get(prefix + files["invalid.webp"].path).status_code == 204


def test_oversized_raster_is_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "large.png"
    with source.open("wb") as file:
        _ = file.seek(30_000_000)
        _ = file.write(b"x")
    with pytest.raises(FileNotFoundError):
        design_thumbnail(source)


def test_camera_photo_exif_orientation_is_applied(tmp_path: Path) -> None:
    source = tmp_path / "camera.jpg"
    photo = Image.new("RGB", (1200, 600), "red")
    exif = Image.Exif()
    exif[274] = 6
    photo.save(source, exif=exif)
    with Image.open(io.BytesIO(design_thumbnail(source))) as thumbnail:
        assert thumbnail.size == (320, 640)
        assert thumbnail.getexif().get(274) is None
