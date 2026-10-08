"""Fixed 10-category IT taxonomy used to file every CV and to route search queries.

The category definitions (labels, role names, title/body keyword weights) live in the shared catalog
(`app/catalog/catalog.json`) so TalentFlow and the warehouse agree on them; this module keeps the
names the rest of the service imports.
"""
from __future__ import annotations

from .catalog import catalog as shared

UNCLASSIFIED = shared.UNCLASSIFIED
Category = shared.Category
CATEGORIES = shared.load_catalog().categories
BY_CODE = shared.category_by_code()
BODY_FACTOR = float(shared.load_catalog().rules["body_factor"])
MIN_SCORE = float(shared.load_catalog().rules["min_score"])

normalize_code = shared.normalize_code
scores = shared.keyword_scores
classify = shared.classify
detect_for_query = shared.detect_for_query
