# backend/tree.py
import os
import json
from datetime import datetime
import re
from slugify import slugify
import logging

def sanitize_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', name)

def save_tree_snapshot(query: str, session_id: str, tree: dict):
    os.makedirs("snapshots", exist_ok=True)
    slug = slugify(query)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"snapshots/{slug}_{session_id}_{timestamp}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(tree, f, indent=2)
    logging.info(f"[SNAPSHOT] Tree saved to {filename}")


def update_tree_branch(tree: dict, path: str, options: dict, ivr_type: str = None) -> dict:
    node = tree
    parts = path.strip().split(".")

    # Traverse to the parent node
    for idx, part in enumerate(parts):
        if idx == 0 and part == "root":
            continue
        if "children" not in node:
            node["children"] = {}
        node = node["children"]
        if part not in node:
            node[part] = {
                "key": ".".join(parts[:idx+1]),
                "label": f"{part}: Unknown",
                "selected": False,
                "ivr_type": None,
                "children": {}
            }
        node = node[part]

    # Set IVR type metadata if passed
    if ivr_type:
        node["ivr_type"] = ivr_type

    # Attach children to this node
    if "children" not in node:
        node["children"] = {}

    for opt_key, label in options.items():
        if opt_key not in node["children"]:
            node["children"][opt_key] = {
                "key": f"{node['key']}.{opt_key}",
                "label": f"{opt_key}: {label}",
                "selected": False,
                "ivr_type": None,
                "children": {}
            }

    return tree
