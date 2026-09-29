"""服务清单的数据模型（设计 5.2 字段表）。

模型只做"字段类型"层面的检查；跨字段、跨服务的检查（重名、依赖、端口范围、路径存在）
放在 registry.validate()，这样界面能把错误就近显示在对应输入框下。
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

Template = Literal["python", "node", "executable", "script", "custom"]
Restart = Literal["on_failure", "always", "no"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Health(_Model):
    """健康检查。不填时：有端口就检查端口能否连上，没有端口就视为"进程在运行即就绪"。"""

    type: Literal["http", "exec"]
    path: str | None = None  # http：请求的路径，返回 2xx 即健康
    command: str | None = None  # exec：退出码为 0 即健康
    initial_delay: int = 1  # 启动后等几秒再开始检查
    period: int = 5  # 每隔几秒检查一次
    timeout: int = 60  # 最多等多少秒变健康，超过算失败

    @model_validator(mode="after")
    def _check_type_fields(self):
        if self.type == "http" and not self.path:
            raise ValueError("http 健康检查需要填写 path")
        if self.type == "exec" and not self.command:
            raise ValueError("exec 健康检查需要填写 command")
        return self


class Group(_Model):
    id: str = Field(pattern=ID_PATTERN)
    name: str


class Service(_Model):
    id: str = Field(pattern=ID_PATTERN)
    name: str
    group: str
    template: Template
    command: str
    template_fields: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    env_file: str | None = None
    port: int | None = None
    health: Health | None = None
    depends_on: list[str] = Field(default_factory=list)
    restart: Restart = "on_failure"
    autostart: bool = True
    shutdown_signal: int = 15
    start_warning: str | None = None

    @field_validator("restart", mode="before")
    @classmethod
    def _yaml_no(cls, v):
        # YAML 里的 restart: no 会被解析成布尔值 False
        if v is False:
            return "no"
        return v

    @field_validator("env", mode="before")
    @classmethod
    def _env_to_str(cls, v):
        # YAML 里 PORT: 8000 会被解析成整数，统一转成字符串
        if isinstance(v, dict):
            return {str(k): ("" if val is None else str(val)) for k, val in v.items()}
        return v


class Registry(_Model):
    version: int = 1
    groups: list[Group] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)

    def service(self, service_id: str) -> Service | None:
        return next((s for s in self.services if s.id == service_id), None)

    def group(self, group_id: str) -> Group | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def services_in_group(self, group_id: str) -> list[Service]:
        return [s for s in self.services if s.group == group_id]


def make_id(name: str, existing: list[str] | set[str], prefix: str = "service") -> str:
    """由显示名称生成 id：英文和数字保留（转小写），其余替换为 -；为空则用 service-N。"""
    existing = set(existing)
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not base:
        n = 1
        while f"{prefix}-{n}" in existing:
            n += 1
        return f"{prefix}-{n}"
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
