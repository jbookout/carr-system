"""Compatibility facade for the former Job Passport-local import path.

Canonical shared evaluation ownership is ``evaluation_kernel``.  This module
intentionally contains no contract or policy logic so legacy callers migrate
without creating a second evaluation source of truth.
"""
from evaluation_kernel import EvalPortfolioError, cost_curve_gate, validate_eval_portfolio

__all__ = ["EvalPortfolioError", "cost_curve_gate", "validate_eval_portfolio"]
