"""Optional buying ladder: decision support for monthly contributions vs drawdown."""

from buying_ladder.allocation import VwceCndxSplitResult, compute_vwce_cndx_split
from buying_ladder.logic import BuyingLadderResult, compute_buying_ladder, display_step_label
from buying_ladder.models import (
    BuyingLadderSettings,
    ContributionPhase,
    LadderStep,
    default_buying_ladder_settings,
    merge_with_defaults,
)
from buying_ladder.storage import load_buying_ladder_settings, save_buying_ladder_settings

__all__ = [
    "VwceCndxSplitResult",
    "BuyingLadderResult",
    "BuyingLadderSettings",
    "ContributionPhase",
    "LadderStep",
    "compute_buying_ladder",
    "compute_vwce_cndx_split",
    "display_step_label",
    "default_buying_ladder_settings",
    "load_buying_ladder_settings",
    "merge_with_defaults",
    "save_buying_ladder_settings",
]
