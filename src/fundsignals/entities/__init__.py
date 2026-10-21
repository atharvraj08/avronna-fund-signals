"""Map documents to funds in the universe (exact, alias and fuzzy name matching)."""

from fundsignals.entities.matcher import FundMatcher, match_documents
from fundsignals.entities.universe import Fund, load_universe

__all__ = ["Fund", "FundMatcher", "load_universe", "match_documents"]
