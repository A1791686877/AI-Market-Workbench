"""Deterministic, conservative semantic checks. Never rewrites an immutable answer.

This is a bounded Chinese/English rule checker, not a claim of general natural-
language understanding. Historical signal validity remains explicit/unknown.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import Field
from app.models import AiAnalysisResult, AnalysisSnapshot, StrictModel


class SemanticIssue(StrictModel):
    code: Literal['PRICE_DIRECTION','LEVEL_ROLE','INVALIDATED_FVG','UNVERIFIED_SIGNAL_VALIDITY','UNSUPPORTED_COMPARISON']
    severity: Literal['ERROR','REVIEW_REQUIRED']
    location: str
    detail: str
    quote: str


class LevelPosition(StrictModel):
    level_id: str
    price: str
    relative_to_last_close: Literal['BELOW','AT','ABOVE']
    declared_role: str


class SemanticReview(StrictModel):
    rule_version: Literal['price-role-signal-zh-en-v1'] = 'price-role-signal-zh-en-v1'
    status: Literal['REJECTED','REVIEW_REQUIRED','CHECKED_SCOPE_ONLY']
    analysis_id: str
    issues: list[SemanticIssue]
    level_positions: list[LevelPosition]
    limits: list[str] = Field(default_factory=lambda:[
        'Checks explicit price ordering, current support/resistance roles and claimed signal activity.',
        'Passing this bounded rule set is not full semantic validation or forecast accuracy.',
        'Historical divergence has no inferred expiry or active status; cannot be used as a current trigger without a frozen rule.',
    ])


NUMBER = r'(?<![\d.])\d[\d,]*(?:\.\d+)?'
DOWN = re.compile(r'(?:跌破|失守|break(?:s)? below)\D{0,12}('+NUMBER+r')', re.I)
UP = re.compile(r'(?:突破|站上|站稳|break(?:s)? above)\D{0,12}('+NUMBER+r')', re.I)


def review_analysis(analysis: AiAnalysisResult, snapshot: AnalysisSnapshot) -> SemanticReview:
    issues = []
    latest = Decimal(snapshot.bars[0].bars[-1].close)
    levels = {level.level_id:Decimal(level.price) for level in analysis.key_levels}
    prices = set(levels.values())
    def recognized(text):
        result=[]
        for match in re.finditer(NUMBER,text):
            value=Decimal(match.group().replace(',',''))
            # Accept ordinary 2-decimal display rounding, but compare exact levels.
            candidates=[p for p in prices if abs(p-value)<=Decimal('.005')]
            if candidates:
                result.append((match.start(), min(candidates,key=lambda p:abs(p-value))))
        return result
    def issue(code,location,detail,quote,severity='ERROR'):
        issues.append(SemanticIssue(code=code,severity=severity,location=location,detail=detail,quote=quote))
    positions=[]
    for level in analysis.key_levels:
        value=Decimal(level.price)
        positions.append(LevelPosition(level_id=level.level_id,price=level.price,
            relative_to_last_close='BELOW' if value<latest else 'ABOVE' if value>latest else 'AT', declared_role=level.kind.value))
        if (level.kind.value=='SUPPORT' and value>latest) or (level.kind.value=='RESISTANCE' and value<latest):
            issue('LEVEL_ROLE',f'key_levels.{level.level_id}',
                  f'{level.kind.value} {value} is on the wrong side of last close {latest}; classify as historical/broken/retest, not current support/resistance.',level.level_id)
    for scenario in analysis.scenarios:
        prefix=scenario.type.value
        texts=[('summary',scenario.summary)] + [(f'preconditions.{i}',c.description) for i,c in enumerate(scenario.preconditions)] + [(f'invalidation.{i}',c.description) for i,c in enumerate(scenario.invalidation_conditions)]
        trigger_text=' '.join(c.description for c in scenario.preconditions)
        trigger_pattern=DOWN if scenario.type.value=='BEARISH' else UP
        trigger=trigger_pattern.search(trigger_text)
        fallback=Decimal(trigger.group(1).replace(',','')) if trigger else latest
        for location,text in texts:
            for clause in re.split(r'[；;。\n]',text):
                direction = 'BELOW' if re.search(r'下方参考|下方目标|下行目标|下一个支撑|downside target|below reference',clause,re.I) else 'ABOVE' if re.search(r'上方参考|上方目标|上行目标|下一个阻力|upside target|above reference',clause,re.I) else None
                if not direction:
                    continue
                explicit=(DOWN if direction=='BELOW' else UP).search(clause)
                anchor=Decimal(explicit.group(1).replace(',','')) if explicit else fallback
                candidates=[value for pos,value in recognized(clause) if not explicit or pos>=explicit.end()]
                if not candidates:
                    issue('UNSUPPORTED_COMPARISON',prefix+'.'+location,'Directional reference could not be resolved to explicit levels.',clause,'REVIEW_REQUIRED')
                for value in candidates:
                    if (direction=='BELOW' and value>=anchor) or (direction=='ABOVE' and value<=anchor):
                        issue('PRICE_DIRECTION',prefix+'.'+location,f'{value} is not {direction} reference price {anchor}.',clause)
            if re.search(r'背离.{0,12}(?:仍然有效|当前有效|继续有效)|(?:active|valid) divergence',text,re.I) and not re.search(r'未|不|非|未知|not |unverified',text,re.I):
                issue('UNVERIFIED_SIGNAL_VALIDITY',prefix+'.'+location,'Historical divergence confirmation does not establish current activity.',text,'REVIEW_REQUIRED')
            if re.search(r'FVG.{0,15}(?:仍有效|未回补|未填补)|(?:active|unfilled) FVG',text,re.I):
                cited=set(scenario.evidence_ids)
                for bundle in snapshot.indicator_bundles:
                    for gap in bundle.fvg:
                        if f'ev_fvg_{gap.fvg_id}' in cited and (gap.validity_status.value=='INVALIDATED' or gap.fill_status.value=='FULLY_FILLED'):
                            issue('INVALIDATED_FVG',prefix+'.'+location,f'{gap.fvg_id} is {gap.fill_status.value}/{gap.validity_status.value}.',text)
    return SemanticReview(status='REJECTED' if any(i.severity=='ERROR' for i in issues) else 'REVIEW_REQUIRED' if issues else 'CHECKED_SCOPE_ONLY',
                          analysis_id=analysis.analysis_id,issues=issues,level_positions=positions)
