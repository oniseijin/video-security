from __future__ import annotations

import dataclasses

from video_security.adapters.generic import GenericAdapter


@dataclasses.dataclass
class GoProAdapter(GenericAdapter):
    name: str = "gopro"
