"""
Bash 命令安全分析

借鉴 claw-code BashTool 的多层安全检查管线:
  commandSemantics → destructiveCommandWarning → pathValidation →
  modeValidation → bashSecurity → bashPermissions

核心思路:
- DangerLevel 四级分级: SAFE → CAUTION → DANGEROUS → BLOCKED
- 规则数据库: 类别化正则 + 辅助检测函数（路径/注入/混淆）
- 与 SSRF 模块一致的 API 设计: Policy dataclass + analyze/validate 函数 + 自定义异常

使用示例:
    from app.core.bash_safety import analyze_command, DangerLevel, CommandBlockedError

    assessment = analyze_command("rm -rf /")
    if assessment.level == DangerLevel.BLOCKED:
        print(f"Blocked: {assessment.risk_summary}")
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ==================== Enums & Data Classes ====================


class DangerLevel(Enum):
    """命令危险等级 —— 借鉴 claw-code destructiveCommandWarning 的分级概念"""

    SAFE = "safe"  # 无害命令，自动执行
    CAUTION = "caution"  # 需用户确认
    DANGEROUS = "dangerous"  # 高危，需输入确认短语
    BLOCKED = "blocked"  # 禁止执行


@dataclass(frozen=True)
class DangerRule:
    """单条危险检测规则

    Attributes:
        pattern: 正则表达式（re.IGNORECASE 匹配）
        level: 危险等级
        category: 规则类别 (destructive_fs, shutdown, fork_bomb, kill,
                  perm_dangerous, pipe_download, format_disk, git_dangerous,
                  git_caution, path_sensitive, env_injection, sudo,
                  network_dangerous, obfuscation)
        description: 人类可读的风险说明
    """

    pattern: str
    level: DangerLevel
    category: str
    description: str


@dataclass
class CommandSafetyPolicy:
    """命令安全策略配置

    借鉴 OpenClaw SsrFPolicy 的设计模式。

    Attributes:
        allow_destructive_commands: 完全跳过安全检查（等同于 dangerously_allow）
        allowed_commands: 精确白名单，这些命令永远放行
        blocked_commands: 额外黑名单，追加到内置规则之外
    """

    allow_destructive_commands: bool = False
    allowed_commands: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)


@dataclass
class CommandAssessment:
    """命令安全分析结果

    Attributes:
        level: 危险等级
        command: 原始命令
        matched_rules: 匹配到的规则列表
        risk_summary: 风险摘要（给用户看）
        confirmation_phrase: DANGEROUS 级别时生成的确认短语
    """

    level: DangerLevel
    command: str
    matched_rules: list[DangerRule] = field(default_factory=list)
    risk_summary: str = ""
    confirmation_phrase: str | None = None


# ==================== Exceptions ====================


class CommandBlockedError(ValueError):
    """命令被阻止 —— 借鉴 OpenClaw SsrFBlockedError"""

    def __init__(self, message: str, assessment: CommandAssessment | None = None):
        super().__init__(message)
        self.assessment = assessment


class CommandConfirmationRequired(Exception):
    """命令需要用户确认（CAUTION 或 DANGEROUS 级别）"""

    def __init__(self, assessment: CommandAssessment):
        super().__init__(assessment.risk_summary)
        self.assessment = assessment


# ==================== Rule Database ====================

# 借鉴 claw-code destructiveCommandWarning + commandSemantics 的分类规则
# 从现有 _DANGEROUS_PATTERNS 升级：每个规则现在有 DangerLevel 分级 + category + description

_DANGER_RULES: list[DangerRule] = [
    # ===== BLOCKED: 破坏性文件系统操作 =====
    DangerRule(
        pattern=r"\brm\s+-[rf]{1,2}\b",
        level=DangerLevel.BLOCKED,
        category="destructive_fs",
        description="递归强制删除文件/目录 (rm -rf)",
    ),
    DangerRule(
        pattern=r"\bdel\s+/[fq]\b",
        level=DangerLevel.BLOCKED,
        category="destructive_fs",
        description="强制删除文件 (del /f)",
    ),
    DangerRule(
        pattern=r"\brmdir\s+/s\b",
        level=DangerLevel.BLOCKED,
        category="destructive_fs",
        description="递归删除目录 (rmdir /s)",
    ),
    DangerRule(
        pattern=r"\bdd\s+if=",
        level=DangerLevel.BLOCKED,
        category="destructive_fs",
        description="磁盘低级操作 (dd)",
    ),
    DangerRule(
        pattern=r">\s*/dev/sd",
        level=DangerLevel.BLOCKED,
        category="destructive_fs",
        description="直接写入块设备",
    ),
    # ===== BLOCKED: 关机/重启 =====
    DangerRule(
        pattern=r"\b(shutdown|reboot|poweroff|halt)\b",
        level=DangerLevel.BLOCKED,
        category="shutdown",
        description="关机/重启系统",
    ),
    DangerRule(
        pattern=r"\binit\s+[06]\b",
        level=DangerLevel.BLOCKED,
        category="shutdown",
        description="切换运行级别 (关机/重启)",
    ),
    DangerRule(
        pattern=r"\bsystemctl\s+(poweroff|reboot|halt|suspend)\b",
        level=DangerLevel.BLOCKED,
        category="shutdown",
        description="systemctl 关机/重启",
    ),
    # ===== BLOCKED: Fork 炸弹 =====
    DangerRule(
        pattern=r":\(\)\s*\{.*\};\s*:",
        level=DangerLevel.BLOCKED,
        category="fork_bomb",
        description="Fork 炸弹攻击",
    ),
    # ===== BLOCKED: 格式化磁盘 =====
    DangerRule(
        pattern=r"\b(format|mkfs|diskpart)\b",
        level=DangerLevel.BLOCKED,
        category="format_disk",
        description="格式化磁盘",
    ),
    # ===== BLOCKED: 下载并执行 =====
    DangerRule(
        pattern=r"\b(wget|curl)\s+.*\|\s*(ba)?sh\b",
        level=DangerLevel.BLOCKED,
        category="pipe_download",
        description="下载脚本并直接执行 (管道到 shell)",
    ),
    DangerRule(
        pattern=r"\b(wget|curl)\s+.*\|\s*(python|perl|ruby|node)\b",
        level=DangerLevel.BLOCKED,
        category="pipe_download",
        description="下载脚本并直接执行 (管道到解释器)",
    ),
    # base64 解码 + 执行: echo xxx | base64 -d | sh
    DangerRule(
        pattern=r"\bbase64\s+-d.*\|\s*(ba)?sh\b",
        level=DangerLevel.BLOCKED,
        category="obfuscation",
        description="base64 解码后管道到 shell",
    ),
    # ===== DANGEROUS: 批量杀进程 =====
    DangerRule(
        pattern=r"\b(pkill|killall)\b",
        level=DangerLevel.DANGEROUS,
        category="kill",
        description="批量终止进程",
    ),
    DangerRule(
        pattern=r"\bkill\s+-9\b",
        level=DangerLevel.DANGEROUS,
        category="kill",
        description="强制终止进程 (SIGKILL)",
    ),
    # ===== DANGEROUS: 危险的权限设置 =====
    DangerRule(
        pattern=r"\bchmod\s+777\b",
        level=DangerLevel.DANGEROUS,
        category="perm_dangerous",
        description="设置完全开放的权限 (chmod 777)",
    ),
    DangerRule(
        pattern=r"\bchown\s+root\b",
        level=DangerLevel.DANGEROUS,
        category="perm_dangerous",
        description="更改文件所有者为 root",
    ),
    DangerRule(
        pattern=r"\bchmod\s+[0-7]*7[0-7]*\s+/",
        level=DangerLevel.DANGEROUS,
        category="perm_dangerous",
        description="修改系统目录权限",
    ),
    # ===== DANGEROUS: Git 危险操作 =====
    DangerRule(
        pattern=r"\bgit\s+reset\s+--hard\b",
        level=DangerLevel.DANGEROUS,
        category="git_dangerous",
        description="Git 硬重置 (git reset --hard) —— 丢失未提交的更改",
    ),
    DangerRule(
        pattern=r"\bgit\s+push\s+.*(--force|-f)\b",
        level=DangerLevel.DANGEROUS,
        category="git_dangerous",
        description="Git 强制推送 (--force) —— 覆盖远程历史",
    ),
    DangerRule(
        pattern=r"\bgit\s+clean\s+-[f]*[d]+[f]*",
        level=DangerLevel.DANGEROUS,
        category="git_dangerous",
        description="Git 清理未跟踪文件 (git clean -fd)",
    ),
    # ===== DANGEROUS: 网络危险操作 =====
    DangerRule(
        pattern=r"\bnc\s+.*-(e|l)\b",
        level=DangerLevel.DANGEROUS,
        category="network_dangerous",
        description="netcat 远程 shell (nc -e)",
    ),
    DangerRule(
        pattern=r"\bncat\s+.*-(e|exec)\b",
        level=DangerLevel.DANGEROUS,
        category="network_dangerous",
        description="ncat 远程执行",
    ),
    # ===== CAUTION: Git 需注意操作 =====
    DangerRule(
        pattern=r"\bgit\s+commit\s+--amend\b",
        level=DangerLevel.CAUTION,
        category="git_caution",
        description="修改最近一次提交 (git commit --amend)",
    ),
    DangerRule(
        pattern=r"\bgit\s+rebase\b",
        level=DangerLevel.CAUTION,
        category="git_caution",
        description="Git 变基 (rebase)",
    ),
    DangerRule(
        pattern=r"\bgit\s+stash\s+drop\b",
        level=DangerLevel.CAUTION,
        category="git_caution",
        description="删除 Git stash",
    ),
    DangerRule(
        pattern=r"\bgit\s+branch\s+-D\b",
        level=DangerLevel.CAUTION,
        category="git_caution",
        description="强制删除 Git 分支",
    ),
    # ===== CAUTION: 环境变量注入检测 =====
    DangerRule(
        pattern=r"\$\([^)]*\)",
        level=DangerLevel.CAUTION,
        category="env_injection",
        description="命令替换 $(...) —— 可能是注入攻击",
    ),
    DangerRule(
        pattern=r"`[^`]+`",
        level=DangerLevel.CAUTION,
        category="env_injection",
        description="反引号命令替换 —— 可能是注入攻击",
    ),
    DangerRule(
        pattern=r"\$\{IFS\}",
        level=DangerLevel.CAUTION,
        category="env_injection",
        description="IFS 环境变量绕过 —— 典型的绕过技巧",
    ),
    DangerRule(
        pattern=r"\$\{[A-Z_]+\}",
        level=DangerLevel.CAUTION,
        category="env_injection",
        description="环境变量展开 —— 可能用于混淆命令",
    ),
    # ===== CAUTION: sudo =====
    DangerRule(
        pattern=r"\bsudo\b",
        level=DangerLevel.CAUTION,
        category="sudo",
        description="以 root 权限执行 (sudo)",
    ),
    DangerRule(
        pattern=r"\bsu\s+-",
        level=DangerLevel.CAUTION,
        category="sudo",
        description="切换用户 (su)",
    ),
    # ===== CAUTION: 写入重定向（非追加） =====
    DangerRule(
        pattern=r"[^>]\s*>\s*/[a-z]",
        level=DangerLevel.CAUTION,
        category="redirect_truncate",
        description="覆盖写入系统文件 (>)",
    ),
]


# 敏感路径 —— 借鉴 claw-code pathValidation
_SENSITIVE_PATHS = [
    "/etc/",
    "/proc/",
    "/sys/",
    "/boot/",
    "/root/",
    "~/.ssh",
    "~/.gnupg",
    "/var/log/",
    ".env",
    ".git/config",
    ".gitcredentials",
    "/Windows/System32",
    "C:\\Windows\\System32",
    "/Library/",
    "/System/",
]


# 危险命令前缀（用于精确匹配白名单检查）
_DANGEROUS_COMMAND_PREFIXES = [
    "rm ",
    "del ",
    "rmdir ",
    "dd ",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "mkfs",
    "format",
    "diskpart",
    "kill",
    "pkill",
    "killall",
    "chmod",
    "chown",
    "sudo",
    "su ",
    "nc ",
    "ncat ",
]


def _check_sensitive_paths(command: str) -> list[DangerRule]:
    """检查命令是否访问敏感路径 —— 借鉴 claw-code pathValidation

    结合命令动词判断危险程度：
    - 读操作（cat/head/tail/ls）访问敏感路径 → CAUTION
    - 写/删除操作（rm/mv/cp/>/>>）访问敏感路径 → DANGEROUS 或 BLOCKED
    """
    matched = []
    command_lower = command.lower()

    # 检查是否包含敏感路径
    touched_paths = []
    for sp in _SENSITIVE_PATHS:
        sp_lower = sp.lower()
        if sp_lower in command_lower:
            touched_paths.append(sp)

    if not touched_paths:
        return matched

    # 判断操作类型
    destructive_ops = [
        r"\brm\b",
        r"\bmv\b",
        r"\bcp\b",
        r">",
        r">>",
        r"\bdel\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bdd\b",
        r"\btee\b",
    ]
    read_ops = [
        r"\bcat\b",
        r"\bhead\b",
        r"\btail\b",
        r"\bless\b",
        r"\bmore\b",
        r"\bls\b",
        r"\bdir\b",
        r"\bfind\b",
        r"\bgrep\b",
        r"\becho\b",
    ]

    is_destructive = any(re.search(op, command_lower) for op in destructive_ops)
    is_read = any(re.search(op, command_lower) for op in read_ops)

    for sp in touched_paths:
        if is_destructive:
            # 特殊：rm -rf /etc → BLOCKED
            if re.search(r"\brm\s+-[rf]", command_lower) and sp in (
                "/etc/",
                "/proc/",
                "/sys/",
                "/boot/",
                "/root/",
            ):
                matched.append(
                    DangerRule(
                        pattern=re.escape(sp),
                        level=DangerLevel.BLOCKED,
                        category="path_sensitive",
                        description=f"删除敏感路径: {sp}",
                    )
                )
            else:
                matched.append(
                    DangerRule(
                        pattern=re.escape(sp),
                        level=DangerLevel.DANGEROUS,
                        category="path_sensitive",
                        description=f"修改敏感路径: {sp}",
                    )
                )
        elif is_read:
            # .env / .gitcredentials 读取也需要确认
            if any(s in sp for s in (".env", ".gitcredentials", "~/.ssh", "~/.gnupg")):
                matched.append(
                    DangerRule(
                        pattern=re.escape(sp),
                        level=DangerLevel.DANGEROUS,
                        category="path_sensitive",
                        description=f"读取敏感文件: {sp}",
                    )
                )
            else:
                matched.append(
                    DangerRule(
                        pattern=re.escape(sp),
                        level=DangerLevel.CAUTION,
                        category="path_sensitive",
                        description=f"访问敏感路径: {sp}",
                    )
                )
        else:
            matched.append(
                DangerRule(
                    pattern=re.escape(sp),
                    level=DangerLevel.CAUTION,
                    category="path_sensitive",
                    description=f"涉及敏感路径: {sp}",
                )
            )

    return matched


def _check_chained_danger(command: str) -> list[DangerRule]:
    """检查命令链（&&, ;, ||）中是否包含危险操作

    命令链中只要有一段是危险的，整个命令就算危险。
    """
    # 分割命令链（&&, ||, ;）
    segments = re.split(r"(?:&&|\|\||;)", command)

    # 只要有任何一个段匹配到 blocked/dangerous 规则，整个链就危险
    matched = []
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        for rule in _DANGER_RULES:
            if rule.level in (DangerLevel.BLOCKED, DangerLevel.DANGEROUS):
                if re.search(rule.pattern, segment, re.IGNORECASE):
                    if rule not in matched:
                        matched.append(rule)
    return matched


def _generate_confirmation_phrase(command: str) -> str:
    """为 DANGEROUS 级别命令生成确认短语"""
    import hashlib

    digest = hashlib.sha256(command.encode()).hexdigest()[:8]
    return f"I-understand-{digest}"


# ==================== Core API ====================


def analyze_command(
    command: str,
    policy: CommandSafetyPolicy | None = None,
) -> CommandAssessment:
    """分析命令的安全性 —— 借鉴 claw-code 多层检查管线

    检查顺序（高优先级先）：
    1. 白名单/黑名单精确匹配（policy）
    2. allow_destructive_commands 短路
    3. 内置规则库 regex 匹配
    4. 敏感路径检测
    5. 命令链危险检测

    Args:
        command: 要分析的命令字符串
        policy: 安全策略配置

    Returns:
        CommandAssessment: 包含危险等级、匹配规则、风险摘要
    """
    p = policy or CommandSafetyPolicy()
    command_stripped = command.strip()

    if not command_stripped:
        return CommandAssessment(
            level=DangerLevel.SAFE,
            command=command,
            risk_summary="空命令",
        )

    # 1. 精确白名单
    for allowed in p.allowed_commands:
        if command_stripped.lower() == allowed.lower():
            return CommandAssessment(
                level=DangerLevel.SAFE,
                command=command,
                risk_summary=f"命令在白名单中: {allowed}",
            )

    # 2. 精确黑名单
    for blocked in p.blocked_commands:
        if blocked.lower() in command_stripped.lower():
            rule = DangerRule(
                pattern=re.escape(blocked),
                level=DangerLevel.BLOCKED,
                category="custom",
                description=f"管理员已禁止: {blocked}",
            )
            return CommandAssessment(
                level=DangerLevel.BLOCKED,
                command=command,
                matched_rules=[rule],
                risk_summary=rule.description,
            )

    # 3. allow_destructive_commands 短路
    if p.allow_destructive_commands:
        return CommandAssessment(
            level=DangerLevel.SAFE,
            command=command,
            risk_summary="安全检查已关闭 (allow_destructive_commands=True)",
        )

    # 4. 内置规则库匹配
    all_matched: list[DangerRule] = []
    command_lower = command.lower()

    for rule in _DANGER_RULES:
        try:
            if re.search(rule.pattern, command_lower, re.IGNORECASE):
                all_matched.append(rule)
        except re.error:
            logger.warning(f"Invalid regex in DangerRule: {rule.pattern}")
            continue

    # 5. 敏感路径检测
    path_matches = _check_sensitive_paths(command)
    all_matched.extend(path_matches)

    # 6. 命令链检测
    chain_matches = _check_chained_danger(command)
    for cm in chain_matches:
        if cm not in all_matched:
            all_matched.append(cm)

    # 确定最终等级（取最高）
    if not all_matched:
        return CommandAssessment(
            level=DangerLevel.SAFE,
            command=command,
            risk_summary="未检测到风险",
        )

    # 优先级: BLOCKED > DANGEROUS > CAUTION > SAFE
    level_order = {
        DangerLevel.BLOCKED: 0,
        DangerLevel.DANGEROUS: 1,
        DangerLevel.CAUTION: 2,
    }
    final_level = DangerLevel.SAFE
    for rule in all_matched:
        if level_order.get(rule.level, 99) < level_order.get(final_level, 99):
            final_level = rule.level

    # 构建风险摘要
    descriptions = [r.description for r in all_matched[:3]]
    if len(all_matched) > 3:
        descriptions.append(f"... 及其他 {len(all_matched) - 3} 条风险")
    risk_summary = "; ".join(descriptions)

    # 生成确认短语（仅 DANGEROUS 级别）
    confirm_phrase = None
    if final_level == DangerLevel.DANGEROUS:
        confirm_phrase = _generate_confirmation_phrase(command)

    return CommandAssessment(
        level=final_level,
        command=command,
        matched_rules=all_matched,
        risk_summary=risk_summary,
        confirmation_phrase=confirm_phrase,
    )


def validate_command(
    command: str,
    policy: CommandSafetyPolicy | None = None,
) -> CommandAssessment:
    """验证命令安全性 —— 借鉴 OpenClaw validateUrlSsrF

    SAFE → 返回 CommandAssessment
    BLOCKED → 抛出 CommandBlockedError
    CAUTION / DANGEROUS → 抛出 CommandConfirmationRequired

    Raises:
        CommandBlockedError: 命令被阻止
        CommandConfirmationRequired: 需要用户确认
    """
    assessment = analyze_command(command, policy)

    if assessment.level == DangerLevel.BLOCKED:
        raise CommandBlockedError(
            f"命令被安全策略阻止: {assessment.risk_summary}",
            assessment=assessment,
        )

    if assessment.level in (DangerLevel.CAUTION, DangerLevel.DANGEROUS):
        raise CommandConfirmationRequired(assessment)

    return assessment


def validate_command_or_none(
    command: str,
    policy: CommandSafetyPolicy | None = None,
) -> str | None:
    """便捷方法: 验证命令，安全返回 None，被阻止返回错误消息

    借鉴 OpenClaw validate_url_ssrf_or_none 模式。
    适应工具 execute() 返回字符串错误消息的模式。

    Returns:
        None 如果安全
        str 错误消息（BLOCKED 或需要确认的描述）
    """
    try:
        validate_command(command, policy)
        return None
    except CommandBlockedError as e:
        return str(e)
    except CommandConfirmationRequired as e:
        return f"需要确认: {e.assessment.risk_summary}"
