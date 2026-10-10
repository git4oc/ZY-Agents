"""RuntimeProfileSnapshot —— 不可变 Profile 模型（``03`` §3.3）。

Profile 字段（``03`` §3.3）：:

    schema_version       "zy.runtime-profile.v1"
    profile_version      "rp-{sha256[:24]}"
    content_hash         全量 sha256
    org_unit_id
    subject_user_id      编译主体
    model_instance_id / provider_id / provider_type / provider_name
    model_name / display_name / capabilities[]
    context_window / max_output_tokens
    tools[]              工具定义（name, description, input_schema, output_schema,
                         category, risk_level, approval_required, timeout_ms）
    tool_versions[]
    skills[]             kind: skill | workflow | writing_style
    skill_versions[] / workflow_versions[]

**品牌不进 Profile**（``00`` §12.4 / ``S-60``）：本 schema 不含任何品牌字段。

**Profile 不含任何密钥**（``03`` §3.2 不变量 1）：只含元数据，密钥在 ``host_payload`` 拼接。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.kernel.contract.versions import RUNTIME_PROFILE_SCHEMA_VERSION

#: Profile 内工具的类别（接线点，见 ``00`` §3.3 / §4.4）
ToolCategory = Literal["server", "local", "hybrid"]
#: Skill 类别
SkillKind = Literal["skill", "workflow", "writing_style"]


class ProfileModel(BaseModel):
    """Profile 数据模型基类：内容寻址需要确定性的字段输出，故同样拒绝多余字段。"""

    model_config = ConfigDict(extra="forbid")


class ProfileTool(ProfileModel):
    """编译进 Profile 的工具定义（无密钥、无 handler 实现细节）。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    category: ToolCategory
    risk_level: Literal["read", "write", "destructive"]
    approval_required: bool = False
    timeout_ms: int = 30000


class ProfileSkill(ProfileModel):
    """编译进 Profile 的 Skill 定义（结构化，非纯文本）。"""

    key: str
    name: str
    kind: SkillKind
    version: str
    definition: dict[str, Any] = {}


class ProfileModelInstance(ProfileModel):
    """编译进 Profile 的模型实例元数据（不含 api_key）。"""

    model_instance_id: str
    provider_id: str
    provider_type: str
    provider_name: str = ""
    model_name: str
    display_name: str = ""
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    context_window: int = 0
    max_output_tokens: int = 0


class RuntimeProfileSnapshot(ProfileModel):
    """一份完整的 Runtime Profile 快照（每人一份，内容寻址）。"""

    schema_version: Literal["zy.runtime-profile.v1"] = RUNTIME_PROFILE_SCHEMA_VERSION
    profile_version: str  # "rp-{sha256[:24]}"
    content_hash: str  # 全量 sha256
    org_unit_id: str
    subject_user_id: str

    model: ProfileModelInstance | None = None

    tools: list[ProfileTool] = Field(default_factory=list)
    tool_versions: dict[str, str] = Field(default_factory=dict)  # tool_name -> version

    skills: list[ProfileSkill] = Field(default_factory=list)
    skill_versions: list[str] = Field(default_factory=list)
    workflow_versions: list[str] = Field(default_factory=list)
