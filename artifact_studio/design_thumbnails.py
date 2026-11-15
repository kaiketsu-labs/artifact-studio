"""Compatibility import for :mod:`artifact_studio.designs.thumbnails`.

Keep module identity for integrations that patch or retain the historical import.
New code should import the feature module directly.
"""

import sys

from .designs import thumbnails as _implementation
from .designs.thumbnails import *  # noqa: F403

sys.modules[__name__] = _implementation
