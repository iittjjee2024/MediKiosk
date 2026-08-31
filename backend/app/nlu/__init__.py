"""Clinical NLU: fitted lexicon matcher for interpreting voice transcripts.

Confined to perception, like everything on the AI side of the determinism
boundary. It maps a transcript to one of a closed set of protocol option codes
for the current field, and returns a calibrated confidence that feeds the same
gate every other perception source feeds.
"""
from .clinical_nlu import Match, NLUModel, get_model, reset_model

__all__ = ["Match", "NLUModel", "get_model", "reset_model"]
