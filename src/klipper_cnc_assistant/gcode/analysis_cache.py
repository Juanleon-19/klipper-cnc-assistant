"""Reuse persisted analysis only for the exact uploaded source and analyzer."""
import hashlib

from .analyzer import CURRENT_ANALYSIS_VERSION


def analysis_is_current(operation, text, *, source_sha256=None):
    # Upload replaces sha256 AND clears analysis in one repository transaction.
    # Hash actual content as well: out-of-band edits cannot inherit old analysis.
    return (operation.analisis is not None
            and operation.analisis.analysis_version == CURRENT_ANALYSIS_VERSION
            and operation.sha256 == (source_sha256 or hashlib.sha256(text.encode("utf-8")).hexdigest()))
