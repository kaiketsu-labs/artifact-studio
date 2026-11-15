"""Design source limits match import archives without relaxing ordinary chat uploads."""

import base64

import pytest
from pydantic import ValidationError

from artifact_studio.artifacts import MAX_FILE_SIZE
from artifact_studio.designs.inputs import MAX_REFERENCE_BYTES, DesignFileInput
from artifact_studio.designs.library import DesignCreate
from artifact_studio.designs.references import ReferenceInput
from artifact_studio.models import FileInput
from artifact_studio.storage import decode_attachments


def test_design_boundary_and_shared_chat_limit_stay_distinct() -> None:
    assert MAX_REFERENCE_BYTES == MAX_FILE_SIZE == 50_000_000
    encoded = base64.b64encode(b"x" * MAX_REFERENCE_BYTES).decode()
    file = DesignFileInput(name="Fictional.bin", data_base64=encoded)
    assert (
        len(decode_attachments([file], max_file_bytes=MAX_REFERENCE_BYTES)[0][1])
        == MAX_REFERENCE_BYTES
    )
    assert ReferenceInput(files=[file]).files[0].name == file.name
    assert DesignCreate(name="Fictional", files=[file]).files[0].name == file.name
    with pytest.raises(ValidationError):
        FileInput(name="Ordinary chat.bin", data_base64=encoded)
    del encoded, file
    too_large = DesignFileInput(
        name="Too large.bin",
        data_base64=base64.b64encode(b"x" * (MAX_REFERENCE_BYTES + 1)).decode(),
    )
    with pytest.raises(ValueError, match="50 MB"):
        decode_attachments([too_large], max_file_bytes=MAX_REFERENCE_BYTES)
    del too_large
    chat = FileInput(
        name="Ordinary chat.bin", data_base64=base64.b64encode(b"x" * 25_000_001).decode()
    )
    with pytest.raises(ValueError, match="25 MB"):
        decode_attachments([chat])
    with pytest.raises(ValueError, match="combined attachments"):
        decode_attachments([chat, chat], max_file_bytes=MAX_REFERENCE_BYTES)
