import os
import json
import shutil
import socket
import sys
import urllib.request
import urllib.error

# 1. Base directory for browser user data
BASE = r"D:\AI\Gemi_MCP\core\browser_user_data"

# 2. Check ports: Gemi engine runs on port 18800. We also check port 8000 but verify if it is Gemi.
is_gemi_running = False

# Check port 18800 (actual Gemi Engine port)
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
if s.connect_ex(('127.0.0.1', 18800)) == 0:
    is_gemi_running = True
s.close()

# Check port 8000 (ComfyUI or Gemi client port)
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
port_8000_open = s.connect_ex(('127.0.0.1', 8000)) == 0
s.close()

if port_8000_open and not is_gemi_running:
    try:
        # Check if port 8000 is Gemi by querying /health
        req = urllib.request.Request("http://127.0.0.1:8000/health")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            res_data = json.loads(response.read().decode())
            if isinstance(res_data, dict) and ("status" in res_data or "engine_running" in res_data):
                is_gemi_running = True
    except Exception:
        pass

if is_gemi_running:
    print("ENGINE RUNNING - ABORT")
    sys.exit(1)

# Check if already reorganized
already_reorganized = False
local_state_path = BASE + "/Local State"
local_state_bak_path = BASE + "/Local State.bak"

if os.path.exists(local_state_path):
    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            state_check = json.load(f)
        ic_keys = list(state_check.get("profile", {}).get("info_cache", {}).keys())
        # If Profile 23 exists in info_cache, and Profile 27 does not, and Profile 27 directory is not on disk,
        # it is already reorganized.
        if "Profile 23" in ic_keys and "Profile 27" not in ic_keys and not os.path.exists(BASE + "/Profile 27"):
            already_reorganized = True
    except Exception:
        pass

if already_reorganized:
    print("Detected: Chrome profiles are ALREADY reorganized.")
    # Ensure Profile 16 directory exists (recreate it if it was deleted)
    p16_path = BASE + "/Profile 16"
    if not os.path.exists(p16_path):
        os.makedirs(p16_path, exist_ok=True)
        print("Recreated missing Profile 16 directory on disk.")
    
    # Load state for verification
    with open(local_state_path, "r", encoding="utf-8") as f:
        state = json.load(f)
    new_ic = state["profile"]["info_cache"]
else:
    # 3. Backup: shutil.copy2(BASE + "/Local State", BASE + "/Local State.bak")
    if os.path.exists(local_state_path):
        shutil.copy2(local_state_path, local_state_bak_path)
    else:
        print(f"Error: {local_state_path} does not exist.")
        sys.exit(1)

    # 4. Delete: shutil.rmtree(BASE + "/Profile 16") and shutil.rmtree(BASE + "/Profile 26")
    for p in ["/Profile 16", "/Profile 26"]:
        path = BASE + p
        if os.path.exists(path):
            shutil.rmtree(path)

    # 5. Rename Map mapping definitions
    RENAME_MAP = {
        2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8, 10: 9,
        11: 10, 12: 11, 13: 12, 14: 13, 15: 14, 17: 15, 18: 16,
        19: 17, 20: 18, 21: 19, 22: 20, 24: 21, 25: 22, 27: 23
    }

    # 6. For old_n, new_n in sorted(RENAME_MAP.items()): rename "Profile {old_n}" to "Profile {new_n}"
    for old_n, new_n in sorted(RENAME_MAP.items()):
        src = BASE + f"/Profile {old_n}"
        dst = BASE + f"/Profile {new_n}"
        if os.path.exists(src):
            os.rename(src, dst)

    # 7. Load Local State JSON from BASE + "/Local State"
    with open(local_state_path, "r", encoding="utf-8") as f:
        state = json.load(f)

    # 8. Get info_cache dictionary
    ic = state["profile"]["info_cache"]

    # 9. Build new_ic = {} : for each key "Profile N" in ic where N is in RENAME_MAP,
    #    add entry new_ic["Profile {RENAME_MAP[N]}"] = ic["Profile N"]. Skip Profile 1, Profile 16, Profile 26.
    new_ic = {}
    for key, value in ic.items():
        if key in ["Profile 1", "Profile 16", "Profile 26"]:
            continue
        if key.startswith("Profile "):
            try:
                n = int(key.split()[1])
                if n in RENAME_MAP:
                    new_key = f"Profile {RENAME_MAP[n]}"
                    new_ic[new_key] = value
            except (ValueError, IndexError):
                pass

    # 10. Update state info_cache and last_used values
    state["profile"]["info_cache"] = new_ic
    state["profile"]["last_used"] = "Profile 23"

    # 11. Write back: open(BASE + "/Local State", "w", encoding="utf-8") and write json.dumps(state, ensure_ascii=False, indent=3)
    with open(local_state_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, ensure_ascii=False, indent=3))

# Helper function to sort profile keys numerically for clean verification output
def sort_key(name):
    try:
        return int(name.split()[1])
    except (IndexError, ValueError):
        return 999

# 12. Print verification: sorted list of Profile dirs, sorted list of info_cache keys, and profile.last_used value
profile_dirs = [d for d in os.listdir(BASE) if os.path.isdir(os.path.join(BASE, d)) and d.startswith("Profile ")]
profile_dirs.sort(key=sort_key)

info_cache_keys = list(new_ic.keys())
info_cache_keys.sort(key=sort_key)

print("Verification Output:")
print("Sorted Profile Directories on Disk:")
print(profile_dirs)
print("Sorted info_cache Keys in Local State:")
print(info_cache_keys)
print("profile.last_used Value:", state["profile"]["last_used"])
