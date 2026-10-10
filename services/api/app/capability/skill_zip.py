"""Skill ZIP 安装包校验（阶段 2 任务：Skill 安装接真）。

只做**纯校验与解析**，不碰数据库（落库在 admin.py 端点）。错误以
``SkillZipError``（含 code / file / field）抛出，端点层转成
FastAPI ``HTTPException(status_code=400, detail={...})``——前端
``skillInstallError.ts`` 契约是 ``response.data.detail.code``。

校验规则（对齐前端 ``skills.install_error.*`` 全量错误码）：
- ZIP 有效、非加密、单文件 ≤ 10 MiB、解压总量 ≤ 20 MiB、条目 ≤ 256；
- 路径安全（无 ``..`` / 绝对路径 / 反斜杠 / 盘符）、无重复路径；
- 至少一个 SKILL.md：单个 → 普通技能包（根目录或单个顶层目录）；
  多个 → 专家包（最浅层级为主技能，其余为子技能 children）；
- SKILL.md 为 UTF-8，含 YAML frontmatter（name 必填 kebab-case、
  description 必填），正文非空，version（可选）符合 SemVer。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: 大小与数量限制（对齐前端 skills.install_error.* 文案）
MAX_ZIP_BYTES = 5 * 1024 * 1024  # 5 MiB（前端 skills.install_limit）
MAX_FILE_BYTES = 10 * 1024 * 1024  # 单文件 10 MiB
MAX_EXPANDED_BYTES = 20 * 1024 * 1024  # 解压总量 20 MiB
MAX_FILES = 256

#: kebab-case（DSH 命名规范）
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
#: SemVer（基础三段）
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.-]+)?$")


class SkillZipError(Exception):
    """ZIP 安装包校验失败（code 对齐前端 skillInstallError.ts MESSAGE_KEYS）。"""

    def __init__(self, code: str, *, file: str = "", field: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.file = file
        self.field = field


@dataclass(frozen=True, slots=True)
class SkillPackageChild:
    """专家包子技能（ZIP 内额外 SKILL.md 的解析结果）。"""

    slug: str
    name: str
    description: str
    version: str
    body: str


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """解析后的 Skill 安装包。"""

    name: str
    description: str
    version: str  # frontmatter 缺省时 "0.1.0"
    body: str  # SKILL.md frontmatter 之后的指令正文
    file_count: int
    files: list[str] = field(default_factory=list)
    sha256: str = ""  # SKILL.md 内容摘要（包级 digest 用安装包字节流，见端点）
    kind: str = "ordinary"  # ordinary（单技能） | expert_package（多技能专家包）
    children: list["SkillPackageChild"] = field(default_factory=list)


def _safe_path(name: str) -> bool:
    """ZIP 条目路径安全（拒绝 ``..`` / 绝对路径 / 反斜杠 / 盘符）。"""
    if "\\" in name or (len(name) > 1 and name[1] == ":"):
        return False
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        return False
    return True


def _locate_skill_mds(names: list[str]) -> tuple[str, str, list[str]]:
    """定位 SKILL.md 条目，返回 ``(主条目, 顶层前缀, 子技能条目列表)``。

    - 单个 SKILL.md：普通技能包（根目录或单个顶层目录，DSH 规范）。
    - 多个 SKILL.md：专家包——主技能取**最浅层级**（根目录优先，其次顶层目录；
      同深度取排序最前），其余 SKILL.md 解析为子技能（children）。
    """
    candidates = [n for n in names if n.upper().endswith("SKILL.MD")]
    if not candidates:
        raise SkillZipError("missing_skill_md")
    if len(candidates) == 1:
        entry = candidates[0]
        parts = PurePosixPath(entry).parts
        if len(parts) == 1:  # 根目录
            return entry, "", []
        if len(parts) == 2 and parts[1].upper() == "SKILL.MD":  # 单个顶层目录
            return entry, parts[0], []
        raise SkillZipError("invalid_bundle_structure")

    def _depth(name: str) -> int:
        return len(PurePosixPath(name).parts)

    ordered = sorted(candidates, key=lambda n: (_depth(n), n))
    main = ordered[0]
    main_parts = PurePosixPath(main).parts
    if len(main_parts) == 1:
        return main, "", ordered[1:]
    if len(main_parts) == 2 and main_parts[1].upper() == "SKILL.MD":
        return main, main_parts[0], ordered[1:]
    raise SkillZipError("invalid_bundle_structure")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter，返回 ``(meta, body)``。"""
    if not text.startswith("---"):
        raise SkillZipError("missing_frontmatter")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if match is None:
        raise SkillZipError("invalid_frontmatter")
    import yaml

    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillZipError("invalid_frontmatter") from exc
    if not isinstance(meta, dict):
        raise SkillZipError("invalid_frontmatter")
    return meta, match.group(2).strip()


def _read_and_parse_skill(zf: zipfile.ZipFile, entry: str) -> tuple[dict, str, bytes]:
    """读取单个 SKILL.md：解码 UTF-8 → frontmatter → name/description/body/version 校验。"""
    try:
        raw = zf.read(entry)
    except RuntimeError as exc:  # 加密等
        raise SkillZipError("encrypted_archive", file=entry) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillZipError("skill_md_not_utf8", file=entry) from exc

    meta, body = _parse_frontmatter(text)

    name = str(meta.get("name") or "").strip()
    if not name:
        raise SkillZipError("missing_name", field="name", file=entry)
    if not KEBAB_RE.match(name):
        raise SkillZipError("invalid_skill_name", field="name", file=entry)
    description = str(meta.get("description") or "").strip()
    if not description:
        raise SkillZipError("missing_description", field="description", file=entry)
    if not body.strip():
        raise SkillZipError("empty_skill_body", file=entry)

    version = str(meta.get("version") or "").strip() or "0.1.0"
    if not SEMVER_RE.match(version):
        raise SkillZipError("invalid_version", field="version", file=entry)

    tools = meta.get("tools")
    if tools is not None and not (
        isinstance(tools, list) and all(isinstance(t, str) for t in tools)
    ):
        raise SkillZipError("invalid_tools", field="tools", file=entry)

    return meta, body, raw


def parse_skill_zip(data: bytes) -> SkillPackage:
    """校验并解析 ZIP 安装包，返回 SkillPackage（纯函数，不落库）。

    单个 SKILL.md → kind=ordinary；多个 SKILL.md → kind=expert_package
    （主技能取最浅层级，其余解析为 children 子技能）。
    """
    if len(data) == 0:
        raise SkillZipError("empty_archive")
    if len(data) > MAX_ZIP_BYTES:
        raise SkillZipError("archive_too_large")

    import io

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise SkillZipError("invalid_zip") from exc

    infos = zf.infolist()
    if not infos:
        raise SkillZipError("empty_archive")

    file_entries = [i for i in infos if not i.is_dir()]
    if len(file_entries) > MAX_FILES:
        raise SkillZipError("too_many_files")

    names = [i.filename for i in infos]
    seen: set[str] = set()
    expanded_total = 0
    for info in infos:
        name = info.filename
        if not _safe_path(name):
            raise SkillZipError("unsafe_archive_path", file=name)
        if name in seen:
            raise SkillZipError("duplicate_archive_path", file=name)
        seen.add(name)
        if info.flag_bits & 0x1:  # 加密位
            raise SkillZipError("encrypted_archive", file=name)
        expanded_total += info.file_size
        if info.file_size > MAX_FILE_BYTES:
            raise SkillZipError("file_too_large", file=name)
    if expanded_total > MAX_EXPANDED_BYTES:
        raise SkillZipError("expanded_archive_too_large")

    entry, _prefix, child_entries = _locate_skill_mds(names)

    meta, body, raw = _read_and_parse_skill(zf, entry)
    name = str(meta.get("name") or "").strip()

    children: list[SkillPackageChild] = []
    for child_entry in child_entries:
        child_meta, child_body, _child_raw = _read_and_parse_skill(zf, child_entry)
        child_name = str(child_meta.get("name") or "").strip()
        children.append(
            SkillPackageChild(
                slug=child_name,
                name=child_name,
                description=str(child_meta.get("description") or "").strip(),
                version=str(child_meta.get("version") or "").strip() or "0.1.0",
                body=child_body,
            )
        )

    import hashlib

    return SkillPackage(
        name=name,
        description=str(meta.get("description") or "").strip(),
        version=str(meta.get("version") or "").strip() or "0.1.0",
        body=body,
        file_count=len(file_entries),
        files=names,
        sha256=hashlib.sha256(raw).hexdigest(),
        kind="expert_package" if children else "ordinary",
        children=children,
    )


def semver_tuple(version: str) -> tuple[int, int, int]:
    """SemVer 三段数值（比较用；预发布段忽略）。"""
    core = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if core is None:
        return (0, 0, 0)
    return (int(core.group(1)), int(core.group(2)), int(core.group(3)))
