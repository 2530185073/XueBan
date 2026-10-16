import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from .errors import Problem


class Contracts:
    def __init__(self, path: Path):
        registry = Registry()
        self.ids = {}
        for name in ("states", "data", "requests"):
            doc = json.loads((path / f"{name}.schema.json").read_text())
            self.ids[name] = doc["$id"]
            registry = registry.with_resource(doc["$id"], Resource.from_contents(doc))
        self.registry = registry

    def validate(self, name: str, value: object, *, kind: str = "data") -> None:
        validator = Draft202012Validator(
            {"$ref": f"{self.ids[kind]}#/$defs/{name}"},
            registry=self.registry,
            format_checker=FormatChecker(),
        )
        try:
            validator.validate(value)
        except ValidationError:
            if kind == "requests":
                raise Problem(422, "validation", "invalid_request", "请求不符合一期契约，请检查必填字段、数据类型及允许范围。") from None
            raise Problem(503, "service", "service_unavailable", "服务返回数据未通过契约检查。") from None
