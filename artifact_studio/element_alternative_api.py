"""Compatibility import for :mod:`artifact_studio.designs.element_api`."""

import sys

from .designs import element_api as _implementation
from .designs.element_api import *  # noqa: F403

sys.modules[__name__] = _implementation
