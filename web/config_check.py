"""启动配置校验：一处登记，两处使用。

- `server._lifespan` 启动时调 `validate_config()`：配错就起不来；
- 部署在 `compose up` 之前用**新镜像**跑 `python -m web.config_check`：配错就中止部署，
  旧容器不受影响。两处读的是同一份 `.env`（compose 的 env_file）、同一份代码，判据不会分叉。

新增一项启动校验，只在 `VALIDATORS` 里加一行。校验项只读环境变量、不碰库与网络，
报错信息不得带出密钥值。
"""

from __future__ import annotations

import sys
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parent
if str(_WEB_DIR) not in sys.path:  # `python -m web.config_check` 时 web/ 不在路径上
    sys.path.insert(0, str(_WEB_DIR))

from inter_node_auth import validate_inter_node_secret  # noqa: E402
from peer_client import validate_peer_node_url  # noqa: E402
from routers.auth import validate_fernet_key, validate_jwt_secret  # noqa: E402

VALIDATORS = (
    validate_fernet_key,
    validate_jwt_secret,
    validate_inter_node_secret,
    validate_peer_node_url,
)


def validate_config() -> None:
    """逐项校验，第一项不通过即抛。"""
    for validate in VALIDATORS:
        validate()


if __name__ == "__main__":
    validate_config()  # 不通过即抛：退出码 1，原因在 traceback 最后一行
    print("配置校验通过")
