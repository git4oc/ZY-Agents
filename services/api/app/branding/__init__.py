"""branding —— 品牌与白标（``00`` §12 / ``01`` §4.8）。

- ``settings.py``: 单文档读写 + 幂等 seed（``_id="singleton"``）。
- ``assets.py``: logo/favicon 上传（内容嗅探 + 尺寸校验 + 服务端命名）。
- ``public_view.py``: 公开白名单视图（未认证端点只返回此处字段）。
- ``repository.py``: 单文档集合的数据访问。

**品牌绝不进入 Profile 编译输入**（``00`` §12.4 / ``S-60``）。
**前端不硬编码品牌**（``S-62``）；公开端点只返回白名单字段（``S-63``）。
"""

from app.branding.settings import BrandingSettings, load, seed_if_empty, update

__all__ = ["BrandingSettings", "load", "update", "seed_if_empty"]
