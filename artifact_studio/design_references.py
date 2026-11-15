"""Compatibility import for :mod:`artifact_studio.designs.references`.

Keep module identity for integrations that patch or retain the historical import.
New code should import the feature module directly.
"""

import sys

from .designs import references as _implementation
from .designs.references import *  # noqa: F403

sys.modules[__name__] = _implementation
