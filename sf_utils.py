import subprocess
import json
import re

def norm_sf_path(path):
    # Windows \ 全部替换为 /，SF CLI稳定识别
    return path.replace("\\", "/")

def clean_ansi_escape(text):
    # 正则匹配并删除所有ANSI转义序列
    ansi_pattern = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_pattern.sub('', text)

def run_cmd(cmd):
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f"ERROR: {str(e)}"

def get_org_info(alias):
    output = run_cmd("sf org list --json")
    try:
        data = json.loads(output)
        for org in data.get("result", {}).get("nonScratchOrgs", []):
            if org.get("alias") == alias:
                return {
                    "connected": True,
                    "username": org.get("username", "Unknown")
                }
    except:
        pass
    return {"connected": False, "username": ""}

def login_org(alias, instance_url):
    cmd = f"sf org login web --alias {alias} --instance-url {instance_url}"
    return run_cmd(cmd)

def logout_org(alias):
    cmd = f"sf org logout --target-org {alias} --no-prompt"
    return run_cmd(cmd)