// 法律文档版本 —— 单一数据源（Single Source of Truth）
//
// ⚠️ 改版规则（必须三步同时做，否则注册会失败，这是有意为之的"强制同步"安全设计）：
//   1. 替换 src/legal/ 下的 md 文件（如 terms_v4.md → terms_v5.md）
//   2. 改下面的版本号
//   3. 同步后端 web/legal_versions.py 的版本号（必须与此处完全一致）
//
// 后端会校验前端提交的版本号 == 后端当前版本，不匹配则拒绝注册。
// 这样能确保留痕记录的是"用户同意了当时的现行版本"，满足合规举证。

export const TERMS_VERSION = "v5";
export const PRIVACY_VERSION = "v3";

// md 文件名跟随版本号，便于改版时一眼对应
export const TERMS_FILE = "terms_v5.md";
export const PRIVACY_FILE = "privacy_v3.md";
