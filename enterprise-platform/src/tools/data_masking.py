"""
数据脱敏工具

功能：
1. 对敏感字段进行自动脱敏处理
2. 支持多种脱敏规则：
   - 手机号：138****1234
   - 身份证：320***********1234
   - 邮箱：a***@example.com
   - 银行卡：6222 **** **** 1234
3. 记录脱敏字段列表
"""

import re
from typing import Any, ClassVar

from config.settings import settings
from src.tools.base import BaseSecureTool


class DataMaskingTool(BaseSecureTool):
    """数据脱敏工具"""

    name: str = "data_masking"
    description: str = (
        "对查询结果中的敏感字段进行脱敏处理。"
        "输入：查询结果数据（columns + data）和敏感字段列表。"
        "输出：脱敏后的数据。"
    )

    # 敏感字段模式（字段名匹配规则）
    SENSITIVE_PATTERNS: ClassVar[list[str]] = [
        r".*phone.*",      # 手机号
        r".*mobile.*",      # 手机号
        r".*id_card.*",     # 身份证
        r".*email.*",       # 邮箱
        r".*bank.*",        # 银行卡
        r".*salary.*",      # 薪资
        r".*password.*",    # 密码
        r".*ssn.*",         # 社保号
    ]

    # 脱敏函数映射
    MASKING_RULES: ClassVar[dict] = {
        "phone": lambda v: _mask_phone(v),
        "mobile": lambda v: _mask_phone(v),
        "id_card": lambda v: _mask_id_card(v),
        "email": lambda v: _mask_email(v),
        "bank": lambda v: _mask_bank_card(v),
        "salary": lambda v: _mask_salary(v),
        "password": lambda v: "******",
        "ssn": lambda v: "******",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sensitive_fields = kwargs.get(
            "sensitive_fields",
            settings.sensitive_fields,
        )

    def _check_permission(self, resource: str = "") -> bool:
        """脱敏工具不需要权限校验"""
        return True

    def _identify_sensitive_fields(self, columns: list[str]) -> dict[str, str]:
        """
        识别敏感字段

        Args:
            columns: 列名列表

        Returns:
            {列名: 脱敏规则类型} 的映射
        """
        sensitive_map = {}
        for col in columns:
            for pattern in self.SENSITIVE_PATTERNS:
                if re.match(pattern, col, re.IGNORECASE):
                    # 提取规则类型
                    rule_type = pattern.replace(".*", "").replace("\\", "").replace("(", "").replace(")", "")
                    sensitive_map[col] = rule_type
                    break
        return sensitive_map

    async def _execute(
        self,
        columns: list[str],
        data: list[list],
        sensitive_fields: list[str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        执行数据脱敏

        Args:
            columns: 列名列表
            data: 二维数据列表
            sensitive_fields: 指定敏感字段列表（可选，不传则自动识别）

        Returns:
            {
                "columns": ["列名"],
                "data": [[脱敏后数据]],
                "masked_fields": ["被脱敏的字段名"],
                "row_count": 行数
            }
        """
        self._log_access("mask", columns=str(columns)[:200])

        # 识别敏感字段
        if sensitive_fields is not None:
            sensitive_map = {}
            for col in columns:
                for sf in sensitive_fields:
                    if sf.lower() in col.lower():
                        sensitive_map[col] = sf
                        break
        else:
            sensitive_map = self._identify_sensitive_fields(columns)

        masked_fields = []

        # 脱敏处理
        masked_data = []
        for row in data:
            masked_row = []
            for i, value in enumerate(row):
                col_name = columns[i] if i < len(columns) else ""

                if col_name in sensitive_map and value is not None:
                    rule_type = sensitive_map[col_name]
                    mask_func = self.MASKING_RULES.get(rule_type)
                    if mask_func:
                        masked_row.append(mask_func(str(value)))
                    else:
                        masked_row.append("******")
                    if col_name not in masked_fields:
                        masked_fields.append(col_name)
                else:
                    masked_row.append(value)

            masked_data.append(masked_row)

        self._log_access("mask_complete", masked_fields=str(masked_fields))

        return {
            "columns": columns,
            "data": masked_data,
            "masked_fields": masked_fields,
            "row_count": len(masked_data),
        }


# ========== 脱敏函数 ==========

def _mask_phone(value: str) -> str:
    """手机号脱敏：138****1234"""
    if len(value) >= 11:
        return value[:3] + "****" + value[-4:]
    return value[:3] + "****" if len(value) > 3 else "****"


def _mask_id_card(value: str) -> str:
    """身份证脱敏：320***********1234"""
    if len(value) >= 18:
        return value[:3] + "***********" + value[-4:]
    return "******"


def _mask_email(value: str) -> str:
    """邮箱脱敏：a***@example.com"""
    if "@" in value:
        local, domain = value.split("@", 1)
        if len(local) > 3:
            return local[0] + "***" + "@" + domain
        return "***@" + domain
    return "****"


def _mask_bank_card(value: str) -> str:
    """银行卡脱敏：6222 **** **** 1234"""
    clean = value.replace(" ", "")
    if len(clean) >= 16:
        return clean[:4] + " **** **** " + clean[-4:]
    return "****"


def _mask_salary(value: str) -> str:
    """薪资脱敏：显示范围而非精确值"""
    # 对于薪资，简单返回脱敏标记
    return "****"