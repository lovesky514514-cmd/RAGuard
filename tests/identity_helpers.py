"""测试使用的可信编排器替身，不对外暴露签名密钥。"""

from raguard.identity import VerifiedIdentity


def trusted(raw: dict, business_ref: str | None = None) -> VerifiedIdentity:
    """同一个任务步骤使用同一业务引用，不同步骤使用独立引用。"""
    if business_ref is None:
        business_ref = f"{raw['task']}-{raw['tool']}-{raw['step']}"
    return VerifiedIdentity(business_ref)
