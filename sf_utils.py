import platform
import subprocess
import json
import re
import streamlit as st

# 固定你本地 sf 绝对路径
SF_FULL_PATH = r"C:\Program Files\sf\bin\sf.cmd"

def norm_sf_path(path):
    return path.replace("\\", "/")

def clean_ansi_escape(text):
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

# 禁用缓存，每次实时查询
@st.cache_data(ttl=0, show_spinner=False)
def get_org_info(target_alias):
    try:
        # 执行sf命令
        proc = subprocess.run(
            [SF_FULL_PATH, "org", "list", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        raw_out = clean_ansi_escape(proc.stdout.strip())
        # 解析顶层JSON
        root = json.loads(raw_out)
        if not isinstance(root, dict):
            return {"connected": False, "username": "", "orgId": "", "error": "顶层JSON不是字典"}

        res_root = root.get("result", {})
        if not isinstance(res_root, dict):
            return {"connected": False, "username": "", "orgId": "", "error": "result节点不是字典"}

        # 合并两个数组
        arr_other = res_root.get("other", [])
        arr_non_scratch = res_root.get("nonScratchOrgs", [])
        org_list = []
        if isinstance(arr_other, list):
            org_list.extend(arr_other)
        if isinstance(arr_non_scratch, list):
            org_list.extend(arr_non_scratch)

        # ========== 新增去重逻辑：通过 orgId 唯一标识 Org ==========
        seen_org_ids = set()
        unique_orgs = []
        for org in org_list:
            if isinstance(org, dict):
                org_id = org.get("orgId", "")
                if org_id and org_id not in seen_org_ids:
                    seen_org_ids.add(org_id)
                    unique_orgs.append(org)
        # ========================================================

        target_clean = target_alias.strip()
        # 遍历去重后的列表匹配别名
        for org_item in unique_orgs:
            item_alias = org_item.get("alias", "").strip()
            if item_alias == target_clean:
                return {
                    "connected": org_item.get("connectedStatus", "") == "Connected",
                    "username": org_item.get("username", ""),
                    "orgId": org_item.get("orgId", "")
                }
        # 遍历完没找到匹配别名
        return {"connected": False, "username": "", "orgId": "", "error": f"未找到别名 {target_alias}"}
    except Exception as err:
        # 捕获全部异常
        return {"connected": False, "username": "", "orgId": "", "error": str(err)}

def login_org(alias, instance_url):
    cmd = f"sf org login web --alias {alias} --instance-url {instance_url}"
    return run_cmd(cmd)

def logout_org(alias):
    cmd = f"sf org logout --target-org {alias} --no-prompt"
    return run_cmd(cmd)