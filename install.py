import os
import shutil
import json
import xml.etree.ElementTree as ET
import sys
import datetime

# --- Defaults ---
DEFAULT_CONFIG = {
    "json": { "*": { "append_keys": ["objectSpawnersArr", "playerRestrictedAreaFiles"] } },
    "xml": { "*": { "strategy": "collection", "id_attributes": ["name", "pos"] } }
}

def load_config():
    if os.path.exists("install_config.json"):
        try:
            with open("install_config.json", "r") as f: return json.load(f)
        except Exception as e: print(f"Config Error: {e}")
    return DEFAULT_CONFIG

def get_file_config(config, file_path):
    # Normalize path separators
    file_path = file_path.replace("\\", "/")
    filename = os.path.basename(file_path)
    extension = os.path.splitext(filename)[1]

    # Priority 1: Exact Path
    if file_path in config: return config[file_path]

    # Priority 2: Wildcard Path (e.g. custom/*.json)
    dir_name = os.path.dirname(file_path)
    if dir_name:
        wildcard_path = f"{dir_name}/*{extension}"
        if wildcard_path in config: return config[wildcard_path]

    # Priority 3: Filename
    if filename in config: return config[filename]

    # Priority 4: Extension Wildcard
    if f"*{extension}" in config: return config[f"*{extension}"]

    return config.get("*", {})

# --- Helpers ---
def make_hashable(value):
    if isinstance(value, list): return tuple(make_hashable(v) for v in value)
    if isinstance(value, dict): return tuple(sorted((k, make_hashable(v)) for k, v in value.items()))
    return value

def create_backup(file_path):
    if os.path.exists(file_path):
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try: shutil.copy2(file_path, f"{file_path}.{ts}.bak")
        except: pass

# --- JSON Logic ---
def deep_merge_json(target, source, append_keys, match_by):
    for key, value in source.items():
        if key in target:
            if isinstance(target[key], dict) and isinstance(value, dict):
                deep_merge_json(target[key], value, append_keys, match_by)
            elif isinstance(target[key], list) and isinstance(value, list):
                if key in append_keys:
                    if key in match_by:
                        id_fields = match_by[key]
                        target_map = {}
                        for idx, item in enumerate(target[key]):
                            if isinstance(item, dict):
                                # Use tuple of values as ID
                                item_id = tuple(make_hashable(item.get(f)) for f in id_fields)
                                target_map[item_id] = idx

                        for item in value:
                            if isinstance(item, dict):
                                item_id = tuple(make_hashable(item.get(f)) for f in id_fields)
                                if item_id in target_map:
                                    # Update existing
                                    idx = target_map[item_id]
                                    deep_merge_json(target[key][idx], item, append_keys, match_by)
                                else: target[key].append(item)
                            elif item not in target[key]: target[key].append(item)
                    else:
                        for item in value:
                            if item not in target[key]: target[key].append(item)
                else: target[key] = value # Overwrite
            else: target[key] = value
        else: target[key] = value
    return target

# --- XML Logic ---
def get_node_id(node, strategy, id_attrs, property_tags):
    tag = node.tag.lower()

    # Strategy: Settings (Always match by Tag)
    if strategy == "settings": return tag

    # Strategy: Property Tag (Force match by Tag to allow overwrite)
    if property_tags and tag in property_tags: return tag

    # Strategy: Collection (Match by Attributes)
    parts = [tag]
    found_attr = False

    if id_attrs:
        for attr in id_attrs:
            if attr in node.attrib:
                parts.append(f"{attr}={node.attrib[attr]}")
                found_attr = True

    # Fallback: If no ID attributes matched, use ALL attributes
    if not found_attr:
        for k, v in sorted(node.attrib.items()):
            parts.append(f"{k}={v}")

    return "|".join(parts)

def recursive_xml_merge(target, source, strategy, id_attrs, property_tags):
    target_map = {}
    for child in target:
        ident = get_node_id(child, strategy, id_attrs, property_tags)
        target_map[ident] = child

    for child in source:
        ident = get_node_id(child, strategy, id_attrs, property_tags)

        if ident in target_map:
            target_child = target_map[ident]
            # Update Attributes
            target_child.attrib.update(child.attrib)
            # Update Text
            if child.text and child.text.strip():
                target_child.text = child.text
            # Recurse
            recursive_xml_merge(target_child, child, strategy, id_attrs, property_tags)
        else:
            target.append(child)

# --- Main Logic ---
def find_mission_data_folder():
    exclusions = {".git", ".github", "__pycache__", ".idea", ".vscode"}
    candidates = []
    for item in os.listdir("."):
        if os.path.isdir(item) and item not in exclusions:
            if item.startswith("dayzOffline"): candidates.append(item)
    if not candidates:
        for item in os.listdir("."):
             if os.path.isdir(item) and item not in exclusions: candidates.append(item)
    if not candidates: sys.exit("Error: No mission data folder found.")
    candidates.sort(key=lambda x: not x.startswith("dayzOffline"))
    return candidates[0]

def get_mission_path(mission_name):
    print(f"Detected mod data for: {mission_name}")
    path = input("Enter server mission directory path: ").strip().replace('"', '')
    if not os.path.isdir(path): sys.exit(f"Error: Directory '{path}' not found.")
    return path

def process_directory(source_root, target_root, config):
    for root, dirs, files in os.walk(source_root):
        rel_path = os.path.relpath(root, source_root)
        if rel_path == ".": rel_path = ""
        target_dir = os.path.join(target_root, rel_path)

        for d in dirs:
            if not os.path.exists(os.path.join(target_dir, d)): os.makedirs(os.path.join(target_dir, d))

        for filename in files:
            src = os.path.join(root, filename)
            dst = os.path.join(target_dir, filename)
            # Use forward slashes for config lookup
            disp = os.path.join(rel_path, filename).replace("\\", "/")

            rules = get_file_config(config, disp)

            # Case 0: Explicit Overwrite Strategy
            if rules.get("strategy") == "overwrite":
                create_backup(dst)
                shutil.copy(src, dst)
                print(f"  [OVERWRITTEN] {disp}")
                continue

            # Case 1: New File
            if not os.path.exists(dst):
                shutil.copy(src, dst)
                print(f"  [NEW] {disp}")
                continue

            # Case 2: JSON Merge
            if filename.endswith(".json"):
                try:
                    with open(dst, 'r', encoding='utf-8') as f: t_data = json.load(f)
                    with open(src, 'r', encoding='utf-8') as f: s_data = json.load(f)

                    deep_merge_json(t_data, s_data, rules.get("append_keys", []), rules.get("match_by", {}))
                    create_backup(dst)
                    with open(dst, 'w', encoding='utf-8') as f: json.dump(t_data, f, indent=4)
                    print(f"  [MERGED] {disp}")
                except Exception as e: print(f"  [ERROR] {disp}: {e}")

            # Case 3: XML Merge
            elif filename.endswith(".xml"):
                try:
                    strategy = rules.get("strategy", "collection")
                    id_attrs = rules.get("id_attributes", ["name", "pos", "color", "x", "z"])
                    property_tags = rules.get("property_tags", [])

                    ET.register_namespace('', "")
                    t_tree = ET.parse(dst)
                    s_tree = ET.parse(src)

                    recursive_xml_merge(t_tree.getroot(), s_tree.getroot(), strategy, id_attrs, property_tags)
                    create_backup(dst)

                    def indent(elem, level=0):
                        i = "\n" + level * "    "
                        if len(elem):
                            if not elem.text or not elem.text.strip(): elem.text = i + "    "
                            if not elem.tail or not elem.tail.strip(): elem.tail = i
                            for elem in elem: indent(elem, level + 1)
                            if not elem.tail or not elem.tail.strip(): elem.tail = i
                        else:
                            if level and (not elem.tail or not elem.tail.strip()): elem.tail = i
                    indent(t_tree.getroot())
                    t_tree.write(dst, encoding="UTF-8", xml_declaration=True)
                    print(f"  [MERGED] {disp}")
                except Exception as e: print(f"  [ERROR] {disp}: {e}")

            # Case 4: Binary/Other
            else:
                create_backup(dst)
                shutil.copy(src, dst)
                print(f"  [UPDATED] {disp}")

def main():
    print("=== Universal DayZ Mod Installer ===")
    config = load_config()
    data_dir = find_mission_data_folder()
    mission_path = get_mission_path(data_dir)
    print(f"\nScanning {data_dir}...")
    process_directory(data_dir, mission_path, config)
    print("\n=== Installation Complete ===")

if __name__ == "__main__":
    main()