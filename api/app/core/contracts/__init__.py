"""Shared contracts (architecture 8.3): what slices read from each other, owned by the foundation.

Contracts only grow: a field may be added, never removed or renamed while another slice uses it
(`tests/unit/test_foundation_contracts.py` holds the documented fields). Nothing here names a
country, case type or specialty; kinds and labels are short codes and i18n keys from the packs.
"""
