"""Signal extraction: matched documents -> typed compute-bottleneck signals with evidence."""

from fundsignals.signals.extract import extract_signals
from fundsignals.signals.llm import HttpxLLMClient, LLMClassifier, LLMClient
from fundsignals.signals.rules import RULES, Hit, Rule, RuleExtractor

__all__ = [
    "RULES",
    "Hit",
    "HttpxLLMClient",
    "LLMClassifier",
    "LLMClient",
    "Rule",
    "RuleExtractor",
    "extract_signals",
]
