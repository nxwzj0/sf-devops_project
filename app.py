from datetime import datetime, timedelta
import zipfile

import streamlit as st
import subprocess
import os
import shutil
import stat
import json
from sf_utils import get_org_info, login_org, logout_org, clean_ansi_escape,norm_sf_path
import xml.etree.ElementTree as ET

# 回滚缓存文件路径
ROLLBACK_CACHE_FILE = ".rollback_cache.json"

# ====================== 元数据解析 & 销毁XML生成 ======================
def parse_sf_metadata_file(file_path):
    """解析SF元数据，自动拼接 对象名.子组件名，适配字段/验证规则等"""
    ext_type_map = {
        ".object": "CustomObject",
        ".field": "CustomField",
        ".layout": "Layout",
        ".apex": "ApexClass",
        ".trigger": "ApexTrigger",
        ".profile": "Profile",
        ".permissionSet": "PermissionSet",
        ".flow": "Flow",
        ".recordType": "RecordType",
        ".tab": "CustomTab",
        ".validationRule": "ValidationRule",
        ".workflowRule": "WorkflowRule"
    }
    file_ext = os.path.splitext(file_path)[1]
    base_name = os.path.basename(file_path)

    # 处理 -meta.xml 后缀
    if base_name.endswith("-meta.xml"):
        base_name = base_name[:-9]
    base_name = os.path.splitext(base_name)[0]

    # 统一路径分隔符，处理objects下子组件
    file_path_normalized = file_path.replace("\\", "/")
    if "objects/" in file_path_normalized:
        try:
            parts = file_path_normalized.split("/")
            obj_index = parts.index("objects")
            if obj_index + 1 < len(parts):
                object_name = parts[obj_index + 1]
                # 替换为涵盖绝大多数 Salesforce 标准子组件的完整列表：
                sub_dirs = [
                    "fields",
                    "validationRules",
                    "recordTypes",
                    "listViews",  # 👈 这次报错的元凶
                    "webLinks",  # 自定义按钮和链接
                    "compactLayouts",  # 紧凑布局
                    "fieldSets",  # 字段集
                    "sharingReasons",  # 共享原因
                    "layouts",
                    "workflows"
                ]
                if obj_index + 2 < len(parts) and parts[obj_index + 2] in sub_dirs:
                    base_name = f"{object_name}.{base_name}"
        except (ValueError, IndexError):
            pass

    # 后缀匹配元数据类型
    meta_type = ext_type_map.get(file_ext, None)
    # 从XML根标签补充类型
    if not meta_type and os.path.exists(file_path):
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
            tag_full = root.tag
            if "}" in tag_full:
                meta_type = tag_full.split("}")[1]
        except ET.ParseError as e:
            st.warning(f"{t('warn_parse_xml')} {file_path}: {str(e)}")  # 修正：替换硬编码为t()
            meta_type = None
        except FileNotFoundError:
            st.warning(f"{t('warn_file_not_exist')} {file_path}")  # 修正：替换硬编码为t()
            meta_type = None
        except Exception:
            meta_type = None
    if not meta_type:
        meta_type = "Unknown"
    return meta_type, base_name


def generate_destructive_xml(item_list, output_path):
    """
    根据新增文件列表生成 destructiveChanges.xml
    修复了 types 标签嵌套畸变的 Bug
    """
    # 关键兜底：无待删除组件，直接退出，不创建空XML
    if not item_list:
        if os.path.exists(output_path):
            os.remove(output_path)
        return

    ns = "http://soap.sforce.com/2006/04/metadata"
    root = ET.Element("Package", xmlns=ns)

    # 按元数据类型分组
    type_group = {}
    for file_path in item_list:
        m_type, m_fullname = parse_sf_metadata_file(file_path)
        if m_type not in type_group:
            type_group[m_type] = []
        if m_fullname not in type_group[m_type]:  # 新增去重逻辑
            type_group[m_type].append(m_fullname)

    # 组装xml结构（🚨修复点：直接把 types 挂在 root 下面，不要嵌套）
    for meta_type, full_names in type_group.items():
        type_node = ET.SubElement(root, "types")
        for fname in full_names:
            ET.SubElement(type_node, "members").text = fname
        ET.SubElement(type_node, "name").text = meta_type  # 标准结构中，name 放在 members 后面

    ET.SubElement(root, "version").text = "60.0"

    # 格式化xml输出
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def generate_empty_package_xml(output_path):
    ns = "http://soap.sforce.com/2006/04/metadata"
    root = ET.Element("Package", xmlns=ns)
    # 彻底删除 types_node，不需要生成空的 types 标签
    ET.SubElement(root, "version").text = "60.0"

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)

# ====================== 回滚缓存持久化（本地JSON） ======================
def save_rollback_cache(add_items):
    # save_rollback_cache增加时间戳
    cache_data = {
        "rollback_add_items": add_items,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(ROLLBACK_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

def load_rollback_cache():
    if not os.path.exists(ROLLBACK_CACHE_FILE):
        return []
    try:
        with open(ROLLBACK_CACHE_FILE, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
            cache_time = datetime.strptime(cache_data.get("timestamp"), "%Y-%m-%d %H:%M:%S")
            if datetime.now() - cache_time > timedelta(hours=24):
                st.warning(t("warn_cache_expired"))  # 修正：替换硬编码为t()
                return []
        return cache_data.get("rollback_add_items", [])
    except Exception:
        return []

def build_rollback_deploy_dir():
    """
    生成销毁专用空目录 + 销毁文件
    遵循SF官方：空目录 + destructiveChanges.xml 执行删除
    """
    # 销毁文件存放目录（空源目录）
    destructive_root = "./rollback_destruct"
    if os.path.exists(destructive_root):
        safe_delete_dir(destructive_root)
    os.makedirs(destructive_root, exist_ok=True)

    # 销毁文件路径
    destructive_file = os.path.join(destructive_root, "destructiveChanges.xml")
    add_items = load_rollback_cache() or st.session_state.get("rollback_add_items")
    # 生成销毁XML（空列表则不生成）
    generate_destructive_xml(add_items, destructive_file)

    # 目标原始备份目录（固定复用，无需重复拷贝）
    dst_backup_dir = "./retrieve/dst"

    return destructive_root, destructive_file, dst_backup_dir

def build_rollback_mdapi_folder():
    mdapi_root = "./rollback_mdapi"
    if os.path.exists(mdapi_root):
        safe_delete_dir(mdapi_root)
    os.makedirs(mdapi_root, exist_ok=True)

    # 🚨 核心修复：去掉 Post，直接命名为 destructiveChanges.xml
    destructive_file = os.path.join(mdapi_root, "destructiveChanges.xml")
    package_file = os.path.join(mdapi_root, "package.xml")

    add_items = load_rollback_cache() or st.session_state.get("rollback_add_items", [])
    generate_destructive_xml(add_items, destructive_file)
    generate_empty_package_xml(package_file)

    dst_backup = "./retrieve/dst"
    return mdapi_root, destructive_file, package_file, dst_backup

def zip_mdapi_dir(source_dir, zip_output):
    """将MDAPI目录打包为ZIP（SF MDAPI部署标准格式）"""
    import zipfile
    with zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 遍历目录，保留相对路径
        for root, _, files in os.walk(source_dir):
            for file in files:
                file_full = os.path.join(root, file)
                # 计算压缩包内相对路径
                arcname = os.path.relpath(file_full, source_dir)
                zipf.write(file_full, arcname)
    return zip_output

# ====================== 加载国际化语言包 ======================
def load_language_pack():
    lang_file_path = os.path.join("config", "language_pack.json")
    with open(lang_file_path, "r", encoding="utf-8") as f:
        return json.load(f)

LANG_PACK = load_language_pack()

# 翻译函数（支持占位符）
def t(key, **kwargs):
    # 兜底：如果key不存在，返回key本身
    if key not in LANG_PACK[st.session_state["lang"]]:
        return key
    txt = LANG_PACK[st.session_state["lang"]][key]
    if kwargs:
        txt = txt.format(**kwargs)
    return txt

# 初始化语言，默认英文
if "lang" not in st.session_state:
    st.session_state["lang"] = "en"

# ====================== 全局基础配置 ======================
SRC_ALIAS = "src-org"
DST_ALIAS = "dst-org"
SRC_INSTANCE = "https://login.salesforce.com"
DST_INSTANCE = "https://login.salesforce.com"

st.set_page_config(page_title=t("page_title"), layout="wide")

# 全局自定义样式：侧边栏菜单文字字号1.3rem
st.markdown("""
<style>
/* 侧边所有radio菜单文字 */
div[data-testid="stRadio"] label p {
    font-size: 1.3rem !important;
}
/* 侧边栏标题、语言切换文字同步适配 */
section[data-testid="stSidebar"] .stMarkdown p {
    font-size: 1.3rem !important;
}
</style>
""", unsafe_allow_html=True)

# ====================== 侧边栏：Logo + 语言切换 + 菜单 ======================
with st.sidebar:
    st.image("config/INFY.png", width=220)
    st.divider()

    # 语言切换
    lang_labels = [t("lang_en"), t("lang_cn")]  # 修正：替换硬编码为t()
    lang_codes = ["en", "cn"]
    default_idx = 0 if st.session_state["lang"] == "en" else 1
    selected_label = st.radio(t("lang_switch"), lang_labels, horizontal=True, index=default_idx)
    selected_lang_code = lang_codes[lang_labels.index(selected_label)]
    if selected_lang_code != st.session_state["lang"]:
        st.session_state["lang"] = selected_lang_code
        st.rerun()
    st.divider()

    # 功能菜单
    menu_options = [
        ("menu_env", t("menu_env")),
        ("menu_retrieve", t("menu_retrieve")),
        ("menu_diff", t("menu_diff")),
        ("menu_validate", t("menu_validate")),
        ("menu_deploy", t("menu_deploy")),
        ("menu_rollback", t("menu_rollback"))
    ]
    menu_keys = [k for k, label in menu_options]
    menu_labels = [label for k, label in menu_options]
    select_label = st.radio(t("menu_header"), menu_labels)
    active_menu_key = menu_keys[menu_labels.index(select_label)]

# ====================== 页面头部 ======================
st.title(f"🌍 {t('platform_title')}")
st.caption(t("caption"))

# ====================== 环境连接状态面板 ======================
src = get_org_info(SRC_ALIAS)
dst = get_org_info(DST_ALIAS)
src_ok = src["connected"]
dst_ok = dst["connected"]

st.subheader(f"🔗 {t('env_title')}")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"### 🌍 {t('src_org')}")
    if src_ok:
        st.success(f"✅ {t('connected')}\n**{SRC_ALIAS}**\n👤 {src['username']}")
        if st.button(t("disconnect_src")):
            logout_org(SRC_ALIAS)
            st.rerun()
    else:
        st.warning(f"❌ {t('not_connected')}")
        if st.button(t("login_src"), type="primary"):
            login_org(SRC_ALIAS, SRC_INSTANCE)
            st.rerun()

with col2:
    st.markdown(f"### 🇨🇳 {t('dst_org')}")
    if dst_ok:
        st.success(f"✅ {t('connected')}\n**{DST_ALIAS}**\n👤 {dst['username']}")
        if st.button(t("disconnect_dst")):
            logout_org(DST_ALIAS)
            st.rerun()
    else:
        st.warning(f"❌ {t('not_connected')}")
        if st.button(t("login_dst"), type="primary"):
            login_org(DST_ALIAS, DST_INSTANCE)
            st.rerun()

st.divider()
if not (src_ok and dst_ok):
    st.warning(t("need_login_tip"))
    st.stop()

# ====================== 通用工具函数 ======================
import re
def clean_ansi_escape(text):
    ansi_pattern = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_pattern.sub('', text)

def exec_with_scroll_log(cmd, log_panel_height=320, parse_json=False, action_name="Operation", use_json_render=False):
    res = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8"
    )
    raw_log = res.stdout + res.stderr
    clean_log = clean_ansi_escape(raw_log)

    if res.returncode == 0:
        st.success(f"✅ {t('exec_success')}")
    else:
        st.error(f"❌ {t('exec_fail')}")

    # 区分两种JSON场景：org列表 ｜ 部署/拉取操作
    if use_json_render and "--json" in cmd:
        try:
            json_data = json.loads(clean_log)
            # 分支1：org列表
            if "other" in json_data.get("result", {}):
                org_list = json_data["result"]["other"]
                display_rows = []
                for org in org_list:
                    display_rows.append({
                        "Alias": org.get("alias", ""),
                        "Username": org.get("username", ""),
                        "Org Id": org.get("orgId", ""),
                        "Status": org.get("connectedStatus", "")
                    })
                st.markdown(f"**📄 {t('log_expander')}**")
                st.dataframe(display_rows, width="stretch", height=log_panel_height)            # 分支2：retrieve拉取命令，格式化折叠展示JSON
            elif "retrieve start" in cmd:
                st.markdown(f"**📄 {t('log_expander')}**")
                with st.expander(t("view_raw_json_log")):
                    # json.dumps自动缩进格式化
                    formatted_json = json.dumps(json_data, indent=2, ensure_ascii=False)
                    st.code(formatted_json, language="json")
            # 分支3：deploy/validate/rollback删除 才用组件统计渲染
            else:
                render_sf_json_log(clean_log, action_title=t(action_name))
        except Exception:
            st.markdown(f"**📄 {t('log_expander')}**")
            st.code(clean_log)
        return res

    # 旧的parse_json 兼容兜底（保留原有逻辑）
    if parse_json:
        try:
            full_json = json.loads(clean_log)
            org_list = full_json.get("result", {}).get("other", [])
            display_rows = []
            for org in org_list:
                display_rows.append({
                    "Alias": org.get("alias", ""),
                    "Username": org.get("username", ""),
                    "Org Id": org.get("orgId", ""),
                    "Status": org.get("connectedStatus", "")
                })
            st.markdown(f"**📄 {t('log_expander')}**")
            st.dataframe(display_rows, width="stretch", height=log_panel_height)
            return res
        except Exception:
            pass

    # 纯文本日志兜底
    st.markdown(f"**📄 {t('log_expander')}**")
    html_template = f"""
    <div style="height:{log_panel_height}px;overflow-y:auto;overflow-x:auto;border:1px solid #eee;padding:10px;border-radius:4px;background:#f8f9fa;">
        <pre style="white-space:pre;font-family:Consolas,Monospace,monospace;margin:0;font-size:11px;line-height:1.3;">{clean_log}</pre>
    </div>
    """
    st.markdown(html_template, unsafe_allow_html=True)
    return res


def render_sf_json_log(raw_log, action_title):
    try:
        data = json.loads(clean_ansi_escape(raw_log))
        result = data.get("result", {})
        # 关键判断：存在部署执行结果才渲染统计
        if "successCount" not in result:
            st.markdown(f"**📄 {t('log_expander')}**")
            st.code(json.dumps(data, indent=2, ensure_ascii=False), language="json")
            return

        deploy_id = result.get("id", "")
        success_count = result.get("successCount", 0)
        fail_count = result.get("failedCount", 0)
        test_fail = result.get("testFailCount", 0)
        total = success_count + fail_count

        # 顶部成功/失败横幅
        if success_count == total and total > 0:
            st.success(t("deploy_success", deployId=deploy_id))
        elif fail_count > 0:
            st.error(f"{action_title} {t('exec_fail')}")

        # 四列指标（仅当有部署结果才展示）
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(t("log_total_components"), total)
        col2.metric(t("log_success_count"), success_count)
        col3.metric(t("log_failed_count"), fail_count)
        col4.metric(t("log_test_failed"), test_fail)

        # 成功/失败明细展开面板不变
        success_items = result.get("componentSuccesses", [])
        if success_items:
            with st.expander(f"✅ {t('log_view_success_detail')}"):
                success_table = []
                for item in success_items:
                    success_table.append({
                        t("log_action_col"): item.get("action", ""),
                        t("log_type_col"): item.get("componentType", ""),
                        t("log_name_col"): item.get("fullName", "")
                    })
                st.dataframe(success_table, width="stretch")
    except Exception:
        st.markdown(f"**📄 {t('log_expander')}**")
        st.code(clean_ansi_escape(raw_log))

def safe_delete_dir(target_dir):
    if not os.path.exists(target_dir):
        return
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for fname in files:
            fpath = os.path.join(root, fname)
            os.chmod(fpath, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            try:
                os.remove(fpath)
            except PermissionError:
                st.warning(t("warn_file_del", path=fpath))
        for dname in dirs:
            dpath = os.path.join(root, dname)
            try:
                os.rmdir(dpath)
            except PermissionError:
                pass
    try:
        os.rmdir(target_dir)
    except Exception:
        pass

# ====================== 各菜单业务逻辑 ======================
if active_menu_key == "menu_env":
    st.subheader(t("menu_env"))
    exec_with_scroll_log(
        "sf org list --json",
        log_panel_height=380,
        parse_json=True,
        action_name="action_env_check",
        use_json_render=True
    )

elif active_menu_key == "menu_retrieve":
    st.subheader(f"📥 {t('retrieve_title')}")
    safe_delete_dir("./retrieve/src")
    safe_delete_dir("./retrieve/dst")
    os.makedirs("./retrieve/src", exist_ok=True)
    os.makedirs("./retrieve/dst", exist_ok=True)
    st.info(t("clean_tip"))

    col_src, col_dst = st.columns(2)
    with col_src:
        st.markdown(f"#### {t('src_org')} Metadata Retrieve")
        cmd_src = f"sf project retrieve start -o {SRC_ALIAS} -x manifest/package.xml -r retrieve/src"
        exec_with_scroll_log(
            cmd_src,
            log_panel_height=300,
            action_name="action_retrieve_src",
            use_json_render=False
        )
    with col_dst:
        st.markdown(f"#### {t('dst_org')} Metadata Retrieve")
        cmd_dst = f"sf project retrieve start -o {DST_ALIAS} -x manifest/package.xml -r retrieve/dst"
        exec_with_scroll_log(
            cmd_dst,
            log_panel_height=300,
            action_name="action_retrieve_dst",
            use_json_render=False
        )

elif active_menu_key == "menu_diff":
    st.subheader(f"📋 {t('diff_title')}")
    col_add, col_mod, col_del = st.columns(3)
    SRC_ROOT = "./retrieve/src"
    DST_ROOT = "./retrieve/dst"
    list_add = []
    list_mod = []
    list_del = []

    for root, _, files in os.walk(SRC_ROOT):
        for fname in files:
            src_full = os.path.join(root, fname)
            rel = os.path.relpath(src_full, SRC_ROOT)
            dst_full = os.path.join(DST_ROOT, rel)
            if not os.path.exists(dst_full):
                list_add.append(os.path.join("retrieve/src", rel))
            else:
                if os.path.getsize(src_full) != os.path.getsize(dst_full):
                    list_mod.append(os.path.join("retrieve/src", rel))
    for root, _, files in os.walk(DST_ROOT):
        for fname in files:
            dst_full = os.path.join(root, fname)
            rel = os.path.relpath(dst_full, DST_ROOT)
            src_full = os.path.join(SRC_ROOT, rel)
            if not os.path.exists(src_full):
                list_del.append(os.path.join("retrieve/dst", rel))

    with st.spinner(t("spinner_run")):
        with col_add:
            st.markdown(f"### {t('diff_add')}")
            st.code("\n".join(list_add) if list_add else "-")
        with col_mod:
            st.markdown(f"### {t('diff_mod')}")
            st.code("\n".join(list_mod) if list_mod else "-")
        with col_del:
            st.markdown(f"### {t('diff_del')}")
            st.code("\n".join(list_del) if list_del else "-")

        if not list_add and not list_mod and not list_del:
            st.success(f"✅ {t('diff_all_same')}")
        else:
            st.success(f"✅ {t('diff_complete')}")

    # 双缓存：会话 + 本地JSON
    st.session_state["rollback_add_items"] = list_add
    save_rollback_cache(list_add)
    st.info(t("cache_tip", count=len(list_add)))

elif active_menu_key == "menu_validate":
    st.subheader(f"🧪 {t('validate_title')}")
    st.info(t("validate_info"))
    cmd = f"sf project deploy start --source-dir retrieve/src --target-org {DST_ALIAS} --dry-run --test-level RunLocalTests"
    exec_with_scroll_log(
        cmd,
        action_name="action_validate_deploy",
        use_json_render=False
    )

elif active_menu_key == "menu_deploy":
    st.subheader(f"🚀 {t('deploy_title')}")
    st.warning(t("deploy_warn"))
    # 读取回滚/部署缓存
    add_items = load_rollback_cache() or st.session_state.get("rollback_add_items", [])
    if not add_items:
        st.error(t("no_cache_deploy"))
        st.stop()
    confirm_deploy = st.checkbox(t("deploy_check"))
    # 只有勾选确认后，才执行部署+渲染统计面板
    if confirm_deploy:
        cmd = f"sf project deploy start --source-dir retrieve/src --target-org {DST_ALIAS} --test-level RunLocalTests --wait 120 --json"
        exec_with_scroll_log(
            cmd,
            action_name="action_official_deploy",
            use_json_render=True
        )

elif active_menu_key == "menu_rollback":
    st.subheader(f"🔙 {t('rollback_title')}")
    st.warning(t("rollback_warn"))

    add_items = load_rollback_cache() or st.session_state.get("rollback_add_items", [])
    if not add_items:
        st.error(t("no_cache_rollback"))
        st.stop()
    if not os.path.exists("./retrieve/dst"):
        st.error(t("missing_dst_backup"))
        st.stop()

    rollback_step1 = t("diff_add") + f": {len(add_items)} items (destructiveChangesPost.xml)"
    rollback_step2 = "2. " + t("rollback_restore_dst")
    st.info(f"1. {rollback_step1}\n{rollback_step2}")

    mdapi_dir, destructive_file, pkg_file, dst_backup = build_rollback_mdapi_folder()
    mdapi_norm = norm_sf_path(mdapi_dir)
    dst_norm = norm_sf_path(dst_backup)

    st.markdown(f"**{t('destructive_file_path')}:** `{destructive_file}`")
    with st.expander(t("view_destructive_xml")):
        if os.path.exists(destructive_file):
            with open(destructive_file, "r", encoding="utf-8") as f:
                st.code(f.read(), language="xml")
        else:
            st.warning(t("destructive_file_missing"))

    confirm_rollback = st.checkbox(t("rollback_check"))

    if confirm_rollback:
        st.info(t("rollback_step1"))
        cmd_delete = (
            f"sf project deploy start "
            f"--metadata-dir {mdapi_norm} "
            f"--purge-on-delete "
            f"--target-org {DST_ALIAS} "
            f"--wait 120 "
            f"--ignore-warnings "
            f"--verbose "
            f"--json"
        )
        # 步骤1：删除新增元数据 - 用结构化渲染
        exec_with_scroll_log(
            cmd_delete,
            action_name="action_rollback_delete",
            use_json_render=True
        )

        st.divider()
        st.info(t("rollback_step2"))

        has_files_to_restore = False
        for root, dirs, files in os.walk(dst_norm):
            if any(f != "package.xml" for f in files):
                has_files_to_restore = True
                break

        if not has_files_to_restore:
            st.success(t("rollback_no_restore_needed"))
        else:
            cmd_restore = (
                f"sf project deploy start "
                f"--source-dir {dst_norm} "
                f"--target-org {DST_ALIAS} "
                f"--wait 120 "
                f"--ignore-warnings "
                f"--json"
            )
            # 步骤2：恢复原始元数据 - 用结构化渲染
            res = exec_with_scroll_log(
                cmd_restore,
                action_name="action_rollback_restore",
                use_json_render=True
            )

            # 兼容NothingToDeploy逻辑（render_sf_json_log已处理，此处可简化）
            if res.returncode == 0 or "NothingToDeploy" in clean_ansi_escape(res.stderr):
                st.success(t("rollback_nothing_to_deploy"))

        if os.path.exists(mdapi_dir):
            safe_delete_dir(mdapi_dir)