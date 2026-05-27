# Character-distill 开发规范

## 代码提交前必做
1. 运行 `pytest tests/ -v` 确认全绿
2. 新增核心逻辑必须附带对应测试用例
3. 不允许提交含 console.log、print 调试语句的代码（日志用 `print(f"[模块名] ...")` 格式）

## 代码规范
- 前端 card_json 解析统一用 `import { parseCardJson } from '@/utils/card'`，禁止内联 JSON.parse
- 后端用量记录统一用 `from core.utils import try_record_usage`，禁止各类重复实现
- `except Exception: pass` 仅允许用于"不存在就跳过"的场景（如删除集合），其余必须加日志
- 配置文件加载：先找 config.yaml，不存在回退 config.example.yaml
- API 路由必须加 `@limiter.limit()` 限流

## 文件组织
- 测试文件放 `tests/`，不放根目录
- 一次性脚本放 `scripts/`
- 前端公共工具放 `src/utils/`，公共组件放 `src/components/common/`

## 安全
- 不允许硬编码密钥，敏感配置走 .env
- SQL 必须参数化查询，禁止 f-string 拼接
- 异常信息不允许直接返回给用户，用通用提示

## 部署
- 生产用 docker-compose.prod.yml
- .env 必须设 JWT_SECRET（强随机值）、FERNET_KEY（独立）、ALLOWED_ORIGINS（域名）

## 否决记录
- 硬编码密钥 ❌
- f-string 拼 SQL ❌
- 异常详情返回给用户 ❌
