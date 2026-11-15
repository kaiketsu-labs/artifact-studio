"""Compatibility import for :mod:`artifact_studio.designs.transfer`.

Keep module identity for integrations that patch or retain the historical import.
New code should import the feature module directly.
"""

import sys

from .designs import transfer as _implementation
from .designs.transfer import *  # noqa: F403

sys.modules[__name__] = _implementation
