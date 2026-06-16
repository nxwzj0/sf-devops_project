# Salesforce Migration Platform
## 项目介绍
基于 Streamlit + Salesforce SF CLI 搭建的可视化Salesforce元数据自动化运维平台，面向企业多Salesforce Org开发/测试/生产环境，一站式实现**环境连接、元数据拉取、差异对比、部署预检、正式发布、一键回滚**全流程操作。
平台支持中英文双语切换，封装全部复杂SF CLI命令，提供结构化可视化日志、自动生成销毁清单、MDAPI标准回滚机制，降低Salesforce元数据迁移门槛，规避人工命令操作失误风险。

## 核心功能
### 1. Environment Status 环境连接管理
- 可视化登录、登出源Org/目标Org，双环境连通性前置校验
- 结构化表格展示所有授权Org：别名、账号、Org ID、连接状态
- 未完成双Org连接时拦截所有拉取、部署、回滚操作

### 2. Retrieve Metadata 元数据拉取
- 自动清理历史缓存目录，分别拉取源、目标Org完整元数据
- 双日志模式：原生CLI步骤日志 / 格式化高亮JSON日志可切换
- 完整兼容CustomObject、Field、ListView、Layout、ValidationRule等全部子组件目录结构

### 3. Difference Compare 元数据差异对比
- 自动分类三类变更：新增项 / 修改项 / 删除项
- 自动持久化新增元数据缓存，用于回滚销毁清单生成
- 自动解析子组件完整名称（对象.列表视图/对象.字段），适配Salesforce元数据规范
- 缓存写入本地 `.rollback_cache.json`，页面刷新不丢失对比结果

### 4. Deployment Preview 部署预检（Dry Run）
- 模拟部署校验，仅检测报错不修改目标环境
- 结构化统计面板：总组件数、成功组件、失败组件、测试失败数量
- 支持展开查看组件成功/失败明细表格，快速定位依赖、权限问题

### 5. Official Deploy 正式元数据部署
- 高风险黄色警告 + 勾选二次确认，防止生产环境误操作
- 自动读取差异缓存，将源Org变更推送至目标Org
- 部署完成展示结构化统计与组件变更明细

### 6. Rollback 一键回滚（核心特色）
1. 自动生成标准 `destructiveChanges.xml` 销毁清单，删除本次部署全部新增元数据
2. 自动读取 `retrieve/dst` 部署前原始备份，恢复目标Org旧配置
3. 执行前可折叠预览完整销毁XML，提前核对待删除组件
4. 分步执行：删除新增组件 → 恢复原始配置，两步独立日志输出
5. 采用MDAPI销毁规范 `--purge-on-delete`，组件永久删除不进入回收站

## 配套增强能力
1. **全量国际化**
   所有页面标题、按钮、提示、日志表头统一托管 `config/language_pack.json`，中英文一键切换，使用`t("key")`统一翻译渲染。
2. **智能结构化日志渲染**
   - Org列表：表格展示组织连接信息
   - 部署/预检/回滚：指标卡片 + 可展开组件明细
   - 元数据拉取：格式化高亮JSON或原生CLI日志二选一
3. 多层安全兜底校验
   - 无差异缓存时拦截部署、回滚操作
   - 无目标Org备份目录禁止执行回滚恢复
   - 无新增组件自动删除销毁XML，规避Salesforce XML结构校验报错
4. UI自定义优化
   - 侧边栏功能菜单字体固定1.3rem，优化阅读体验
   - 彩色成功/失败提示、滚动日志面板、折叠明细面板
   - 自动清理回滚、拉取临时目录，减少磁盘冗余文件

## 前置环境依赖
### 软件要求
1. Python 3.10 及以上版本
2. Salesforce SF CLI 最新稳定版（配置系统全局环境变量）
3. SF CLI 元数据插件：`sf plugins install salesforce-cli-plugin-metadata`
4. Git（可选，用于项目版本管理）

### Python 依赖清单 dev-requirements.txt
```txt
streamlit
lxml
xmltodict
```

### 项目目录结构
```txt
sf-devops_project/
├── config/
│   ├── INFY.png               # 侧边栏Logo图片
│   ├── language_pack.json     # 中英双语国际化文案配置
│   └── project-scratch-def.json
├── deploy/                     # 部署预留辅助目录
├── manifest/
│   └── package.xml            # 全局元数据拉取基础清单
├── retrieve/
│   ├── src/                   # 源Org元数据拉取缓存
│   └── dst/                   # 目标Org原始备份（回滚依赖核心目录）
├── rollback_mdapi/            # 回滚临时MDAPI打包目录（程序自动生成&清理）
├── .rollback_cache.json       # 差异对比本地持久化缓存文件
├── app.py                     # 项目主程序入口
├── config.py                  # 全局常量、工具函数
├── dev-requirements.txt       # Python依赖清单
└── README.md                  # 项目说明文档
```

### 快速启动流程
1. 环境初始化
```sh
# 安装Python依赖
pip install -r dev-requirements.txt

# 校验SF CLI是否正常识别
sf --version
```
2. 启动可视化平台
```sh
streamlit run app.py
```
启动成功后终端输出访问地址：
本地访问：http://localhost:8501
局域网共享访问：http://192.168.x.x:8501

3. 标准运维操作规范（推荐执行顺序）
```txt
   Environment Status：登录源 Org、目标 Org，确认双连接正常
   Retrieve Metadata：拉取两边完整元数据，生成本地缓存
   Difference Compare：自动计算变更，生成回滚缓存
   Deployment Preview：预检部署，提前排查报错
   Official Deploy：校验无误后执行正式发布
   Rollback（异常故障场景）：一键执行销毁新增组件 + 恢复原始配置
```

### 国际化扩展说明
所有展示文案统一存放在 config/language_pack.json，分为 en / cn 两套键值对；
页面内通过 t("key") 函数自动读取对应语言文本；
新增页面提示仅需在 JSON 中补充对应 key，无需修改页面渲染逻辑；
侧边栏语言单选框切换后页面实时重载翻译，无需重启程序。

### 回滚底层实现原理
差异对比阶段记录所有本次新增元数据文件路径，持久化缓存；
回滚模块自动解析元数据类型、组件全名，生成符合 Salesforce 规范的destructiveChanges.xml；
使用 MDAPI 目录模式执行销毁部署，永久删除新增对象、字段、列表视图等组件；
重新部署retrieve/dst目录内的部署前原始元数据，完成环境还原；
无新增变更时自动跳过销毁步骤，仅执行配置恢复，避免空 XML 部署报错。
