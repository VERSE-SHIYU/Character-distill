"""节点身份的唯一来源。

`NODE_REGION` 这个名字与缺省值**只在本模块出现一次**。取它的两处 —— 注册时写
`home_region`（`web/routers/auth.py`）与上报时打 `region` 标签（`core/error_reporting.py`）
—— 都调 `node_region()`：同一个事实读两遍就会有两份缺省，改一处忘了另一处时，
两处对「本节点是哪个区」的说法就分叉了。
"""
from __future__ import annotations

import os

_ENV = "NODE_REGION"
_DEFAULT = "cn-shenzhen"


def node_region() -> str:
    """本节点的区域标识。`NODE_REGION` 未配置时用缺省（深圳）。"""
    return os.environ.get(_ENV, _DEFAULT)
