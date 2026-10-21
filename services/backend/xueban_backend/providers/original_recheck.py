"""Single-target original-photo reads; old answers/verdicts are not model input."""
from copy import copy, deepcopy
from dataclasses import replace

from .ark import ProviderError
from . import question_units

PROTOCOL = "original-image-recheck.r1"
RULES = (
    "本次只重新核对task_input.original_target定位的一个原有独立作答单元，不处理整页其他题。"
    "定位线索来自历史记录，可能有误；先从原图独立找到实际题号/标题/行列，不得照抄定位线索为题面。"
    "未提供旧学生作答、旧判定或参考答案。只记录这次原图直接可见的原文，再独立判定。"
    "同题内部全部位置必须保留；不能把一个旧空位扩大成整题，不能把整题拆成只含一空。"
    "只输出实际唯一对应的一项；没有找到、范围不明或存在多个候选时items为空。"
    "label与visible_locator记录实际可见的号，不为迎合请求改号。不要输出页面其他项目。"
)


def request_scope(schema, prompt, target):
    if target is None:
        return schema, prompt, {}
    schema = deepcopy(schema)
    schema["properties"]["items"]["maxItems"] = 1
    return schema, prompt + RULES, {"original_target": deepcopy(target)}


def run(pipeline, *, images, question, attempt, input_versions):
    identifiers = attempt.get("asset_ids") or []
    selected = tuple(image for image in images if image.asset_id in identifiers)
    if len(identifiers) != 1 or len(selected) != 1 or selected[0].asset_id != identifiers[0]:
        raise ProviderError("invalid_input")
    local = copy(pipeline)
    local.target_unit = question_units.stored_unit(question)
    context = question.get("target_context") or {}
    # Deliberately exclude stored stem/answer/verdict and the anchor, which can
    # contain a whole incorrectly read condition. These are locator hints only.
    local._original_recheck_target = {
        "label": question.get("label"), "target_unit": local.target_unit,
        "section": context.get("section"), "row": context.get("row"),
        "column": context.get("column"),
    }
    result = local.recognize(images=selected)
    result.data["original_recheck"] = {"protocol": PROTOCOL,
        "input_versions": deepcopy(input_versions), "asset_ids": list(identifiers),
        "target_locator": deepcopy(local._original_recheck_target)}
    return replace(result, prompt_version=result.prompt_version + ".original-recheck-r1")
