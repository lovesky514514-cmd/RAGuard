"""仅针对本地 Go 沙箱的证据适配器；不代表真实第三方签名验真。"""

from __future__ import annotations

import time
import urllib.parse

from .evidence import (EvidenceContract, EvidenceDecision, EvidenceObservation,
                       assess_evidence)
from .model import Call
from .policy import ToolPolicy
from .transport import SandboxClient, UncertainResult


class LocalEvidenceClient(SandboxClient):
    """B5/Gateway 共享同一回读验证步骤，只有确认提交才返回结果。

    可信边界：本地受控进程和预登记的 loopback 来源。这里没有签名或
    防恶意同机进程替换能力，不能直接作为外网 Provider 的安全凭据。
    """

    AUTHORITY = 'local-go-sandbox'
    EPOCH = 1

    def __init__(self, *args, max_age_seconds: float = 60.0, **kwargs):
        super().__init__(*args, **kwargs)
        if not 0 < max_age_seconds <= 3600:
            raise ValueError('证据期限超出本地研究界限')
        self.max_age_seconds = max_age_seconds
        self.admitted = 0
        self.rejected = 0
        self.queries = 0
        self.last_evidence_state = "unknown"

    def readback(self, call: Call, policy: ToolPolicy) -> dict | None:
        if not policy.readback:
            return None
        self.queries += 1
        self.last_evidence_state = "pending"
        query = urllib.parse.urlencode({'tool_id': call.tool,
                                        'intent_key': call.stable_key})
        try:
            body = self._json('/v1/effects?' + query)
        except UncertainResult:
            self.rejected += 1
            return None
        if body.get('status') != 'confirmed':
            self.rejected += 1
            return None
        if body.get('intent_key') != call.stable_key:
            self.last_evidence_state = 'contract_violation'
            self.rejected += 1
            return None
        if not isinstance(body.get('result'), dict):
            self.rejected += 1
            return None
        # 所有字段都来自 Go 返回的证据，不能用请求字段回填缺失的证据。
        required = ('tool_id', 'business_key', 'fingerprint', 'authority',
                    'authority_epoch', 'version', 'observed_at', 'effect_id')
        if (any(not isinstance(body.get(k), str) for k in required[:4])
                or not isinstance(body.get('effect_id'), str)
                or type(body.get('authority_epoch')) is not int
                or type(body.get('version')) is not int
                or type(body.get('observed_at')) not in (float, int)
                or type(body.get('final')) is not bool):
            self.rejected += 1
            return None
        expected_business = (self.research_business_key if self.research_business_key is not None
                             else f'{call.tool}:{call.stable_key}')
        contract = EvidenceContract(
            tool=call.tool, business_ref=expected_business,
            payload_digest=call.fingerprint, authority=self.AUTHORITY,
            authority_epoch=self.EPOCH, max_age_seconds=self.max_age_seconds,
            min_version=1)
        # 错误的业务主体、指纹或权威代次表示此回执不能用于当前操作。
        # 在当前恢复窗口中停止轮询；未来合法请求仍可重新发起查询。
        if (body['tool_id'] != contract.tool or body['business_key'] != contract.business_ref
                or body['fingerprint'] != contract.payload_digest
                or body['authority'] != contract.authority
                or body['authority_epoch'] != contract.authority_epoch):
            self.last_evidence_state = 'contract_violation'
            self.rejected += 1
            return None
        observation = EvidenceObservation(
            tool=body['tool_id'], business_ref=body['business_key'],
            payload_digest=body['fingerprint'], authority=body['authority'],
            authority_epoch=body['authority_epoch'], observed_at=body['observed_at'],
            version=body['version'], outcome='committed',
            effect_id=body['effect_id'],
            # 本地受控沙箱的 HTTP 源地址经构造器限制为 loopback；无远程验签。
            verified=(self.origin.startswith('http://127.0.0.1:')
                      and body['authority'] == self.AUTHORITY),
            final=body['final'])
        result = assess_evidence(contract, (observation,), time.time())
        if result.decision != EvidenceDecision.CONFIRMED:
            self.rejected += 1
            return None
        self.last_evidence_state = 'confirmed'
        self.admitted += 1
        return body['result']
