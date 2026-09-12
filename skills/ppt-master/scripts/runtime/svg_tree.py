#!/usr/bin/env python3
"""
PPT Master - Shared SVG Tree Lookup Helpers (P6)

Promoted out of two independent copies (scripts/svg_editor/annotations.py
and scripts/svg_editor/server.py each had their own private
`_find_by_id`) into one shared implementation -- the same "promote a
duplicated helper" pattern P5 used for slide_id_from_page. Both the
Editor's direct-edit path and P6's Patch Engine (scripts/runtime/patch_engine.py)
resolve elements by id through this module now.

Usage:
    from runtime.svg_tree import find_by_id, find_with_parent

Dependencies:
    None (standard library only)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def find_by_id(root: ET.Element, element_id: str) -> ET.Element | None:
    """Find an element by its id attribute anywhere in the tree."""
    for elem in root.iter():
        if elem.get("id") == element_id:
            return elem
    return None


def find_with_parent(root: ET.Element, element_id: str) -> tuple[ET.Element | None, ET.Element | None]:
    """Find an element and its direct parent by id. (None, None) if absent."""
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == element_id:
                return child, parent
    return None, None
