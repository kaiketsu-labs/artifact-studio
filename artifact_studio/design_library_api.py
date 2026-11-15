"""Compatibility import for :mod:`artifact_studio.designs.library_api`.

Keep module identity for integrations that patch or retain the historical import.
New code should import the feature module directly.
"""

import sys

from .designs import library_api as _implementation
from .designs.library_api import *  # noqa: F403

sys.modules[__name__] = _implementation
