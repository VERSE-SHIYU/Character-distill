# 此文件必须在所有会触发模型加载的 import 之前执行。
# 它设置环境变量、torch 默认设备，并修补 nn.Module.to 以防御 meta tensor。

import os

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("ACCELERATE_CPU_DEVICE", "true")
os.environ.setdefault("ACCELERATE_USE_DEVICE_MAP", "false")

import torch

torch.set_default_device("cpu")
torch.set_default_dtype(torch.float32)


def _patch_nn_module_to():
    """修补 nn.Module.to，防止对 meta 设备模型调用 .to() 时报错。

    当 accelerate 已将模型路由到 meta 设备时，普通的 .to('cpu')
    会触发 "Cannot copy out of meta tensor"。此补丁拦截该情况，
    改用 to_empty() 重建模型在 CPU 上。
    """
    import torch.nn as nn

    _original_to = nn.Module.to

    def _safe_to(self, *args, **kwargs):
        try:
            first_param = next(self.parameters(), None)
        except StopIteration:
            first_param = None

        if first_param is not None and str(first_param.device) == "meta":
            try:
                self.to_empty(device="cpu")
            except Exception:
                pass
            return self
        return _original_to(self, *args, **kwargs)

    nn.Module.to = _safe_to


_patch_nn_module_to()
