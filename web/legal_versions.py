"""法律文档版本 —— 后端单一数据源。

⚠️ 必须与前端 web/frontend/src/legal/versions.js 的版本号完全一致。
改版时两处同步修改（见 versions.js 的说明）。

register 接口会校验前端提交的版本号 == 此处版本号，不匹配则拒绝注册，
确保 user_consent 留痕记录的是用户同意的"当时现行版本"。
"""

CURRENT_TERMS_VERSION = "v4"
CURRENT_PRIVACY_VERSION = "v2"
