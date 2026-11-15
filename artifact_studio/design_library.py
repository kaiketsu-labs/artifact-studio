"""Compatibility import for :mod:`artifact_studio.designs.library`.

Keep module identity for integrations that patch or retain the historical import.
New code should import the feature module directly.
"""

import sys

from .designs import library as _implementation
from .designs.library import *  # noqa: F403

sys.modules[__name__] = _implementation
