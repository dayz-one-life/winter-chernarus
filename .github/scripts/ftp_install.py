import os
import json
import ftplib
import xml.etree.ElementTree as ET
import sys
import io
import datetime

# --- Config Loader ---
DEFAULT_CONFIG = {
    "custom/*.json": { "strategy": "overwrite" },
    "cfggameplay.json": { "append_keys": ["objectSpawnersArr", "playerRestrictedAreaFiles"] },
    "*.xml": { "strategy": "collection", "id_attributes": ["name", "pos"] }
}

def load_config():
    if os.path.exists("install_config.json"):
        try:
            with open("install_config.json", "r") as f: return json.load(f)
        except: pass
    return DEFAULT_CONFIG

def get_file_config(config, file_path):
    file_path = file_path.replace("\\", "/")
    filename = os.path.basename(file_path)
    extension = os.path.splitext(filename)[1]

    if file_path in config: return config[file_path]
    dir_name = os.path.dirname(file_path)
    if dir_name:
        wildcard_path = f"{dir_name}/*{extension}"
        if wildcard_path in config: return config[wildcard_path]
    if filename in config: return config[filename]
    if f"*{extension}" in config: return config[f"*{extension}"]
    return config.get("*", {})

# --- Helpers ---
def make_hashable(value):
    if isinstance(value, list): return tuple(make_hashable(v) for v in value)
    if isinstance(value, dict): return tuple(sorted((k, make_hashable(v)) for k, v in value.items()))
    return value

# --- Merge Logic ---
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
                                item_id = tuple(make_hashable(item.get(f)) for f in id_fields)
                                target_map[item_id] = idx
                        for item in value:
                            if isinstance(item, dict):
                                item_id = tuple(make_hashable(item.get(f)) for f in id_fields)
                                if item_id in target_map:
                                    idx = target_map[item_id]
                                    deep_merge_json(target[key][idx], item, append_keys, match_by)
                                else: target[key].append(item)
                            elif item not in target[key]: target[key].append(item)
                    else:
                        for item in value:
                            if item not in target[key]: target[key].append(item)
                else: target[key] = value
            else: target[key] = value
        else: target[key] = value
    return target

def get_node_id(node, strategy, id_attrs, property_tags):
    tag = node.tag.lower()
    if strategy == "settings": return tag
    if property_tags and tag in property_tags: return tag
    parts = [tag]
    found = False
    if id_attrs:
        for attr in id_attrs:
            if attr in node.attrib:
                parts.append(f"{attr}={node.attrib[attr]}")
                found = True
    if not found:
        for k, v in sorted(node.attrib.items()): parts.append(f"{k}={v}")
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
            target_child.attrib.update(child.attrib)
            if child.text and child.text.strip(): target_child.text = child.text
            recursive_xml_merge(target_child, child, strategy, id_attrs, property_tags)
        else: target.append(child)

def process_json_content(target_bytes, source_path, rules):
    try:
        t_data = json.loads(target_bytes.decode('utf-8'))
        with open(source_path, 'r', encoding='utf-8') as f: s_data = json.load(f)
        deep_merge_json(t_data, s_data, rules.get("append_keys", []), rules.get("match_by", {}))
        return json.dumps(t_data, indent=4).encode('utf-8')
    except: return None

def process_xml_content(target_bytes, source_path, rules):
    try:
        ET.register_namespace('', "")
        t_tree = ET.ElementTree(ET.fromstring(target_bytes.decode('utf-8')))
        s_tree = ET.parse(source_path)
        recursive_xml_merge(t_tree.getroot(), s_tree.getroot(), rules.get("strategy", "collection"), rules.get("id_attributes", []), rules.get("property_tags", []))
        out = io.BytesIO()
        t_tree.write(out, encoding="UTF-8", xml_declaration=True)
        return out.getvalue()
    except: return None

def find_mission_data_folder():
    exclusions = {".git", ".github", "__pycache__", ".idea", ".vscode"}
    candidates = []
    for item in os.listdir("."):
        if os.path.isdir(item) and item not in exclusions:
            if item.startswith("dayzOffline"): candidates.append(item)
    if not candidates:
        for item in os.listdir("."):
             if os.path.isdir(item) and item not in exclusions: candidates.append(item)
    if not candidates: return None
    candidates.sort(key=lambda x: not x.startswith("dayzOffline"))
    return candidates[0]

def run_ftp_install():
    config = load_config()
    data_dir = find_mission_data_folder()
    if not data_dir: sys.exit(1)

    HOST = os.environ.get("FTP_HOST")
    USER = os.environ.get("FTP_USER")
    PASS = os.environ.get("FTP_PASSWORD")
    PORT = int(os.environ.get("FTP_PORT", 21))
    PATH = os.environ.get("MISSION_PATH", "").strip()

    if not HOST or not USER or not PASS or not PATH: sys.exit(1)

    try:
        ftp = ftplib.FTP()
        ftp.connect(HOST, PORT)
        ftp.login(USER, PASS)
        ftp.cwd(PATH)

        for root, dirs, files in os.walk(data_dir):
            rel_path = os.path.relpath(root, data_dir)
            if rel_path == ".": rel_path = ""
            ftp_path = rel_path.replace("\\", "/")

            if ftp_path:
                try: ftp.mkd(ftp_path)
                except: pass

            for fname in files:
                local_file = os.path.join(root, fname)
                remote_path = f"{ftp_path}/{fname}" if ftp_path else fname

                # Check Overwrite Strategy
                rules = get_file_config(config, remote_path)
                if rules.get("strategy") == "overwrite":
                    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    try: ftp.rename(remote_path, f"{remote_path}.{ts}.bak")
                    except: pass
                    with open(local_file, "rb") as f: ftp.storbinary(f"STOR {remote_path}", f)
                    print(f"  [OVERWRITTEN] {remote_path}")
                    continue

                exists = False
                try:
                    ftp.size(remote_path)
                    exists = True
                except: pass

                if not exists:
                    with open(local_file, "rb") as f: ftp.storbinary(f"STOR {remote_path}", f)
                    print(f"  [NEW] {remote_path}")
                    continue

                new_content = None
                if fname.endswith(".json"):
                    r = io.BytesIO()
                    ftp.retrbinary(f"RETR {remote_path}", r.write)
                    new_content = process_json_content(r.getvalue(), local_file, rules)
                elif fname.endswith(".xml"):
                    r = io.BytesIO()
                    ftp.retrbinary(f"RETR {remote_path}", r.write)
                    new_content = process_xml_content(r.getvalue(), local_file, rules)

                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                if new_content:
                    try: ftp.rename(remote_path, f"{remote_path}.{ts}.bak")
                    except: pass
                    ftp.storbinary(f"STOR {remote_path}", io.BytesIO(new_content))
                    print(f"  [MERGED] {remote_path}")
                elif not fname.endswith((".json", ".xml")):
                    try: ftp.rename(remote_path, f"{remote_path}.{ts}.bak")
                    except: pass
                    with open(local_file, "rb") as f: ftp.storbinary(f"STOR {remote_path}", f)
                    print(f"  [UPDATED] {remote_path}")
                else:
                    print(f"  [SKIP] {remote_path}")

        ftp.quit()
    except Exception as e:
        print(f"FTP Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_ftp_install()