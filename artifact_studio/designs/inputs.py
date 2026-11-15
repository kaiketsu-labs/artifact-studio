"""Design source budgets match portable design archives without enlarging chat uploads."""

from pydantic import Field

from ..models import FileInput

MAX_REFERENCE_BYTES = 50_000_000


class DesignFileInput(FileInput):
    """One design source; batches still share the ordinary 50 MB combined limit."""

    data_base64: str = Field(max_length=4 * ((MAX_REFERENCE_BYTES + 2) // 3))
