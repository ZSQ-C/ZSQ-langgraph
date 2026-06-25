"""
审计日志表 ORM模型

记录每次查询的完整链路：
用户问题 → 生成SQL → 安全校验 → 执行结果 → 审核记录
v3.0: 增加 tool_name / tool_input / tool_output_summary / critic_score / critic_result 字段
"""

from sqlalchemy import Boolean, Column, Float, Integer, JSON, String, Text

from src.db.database import Base


class AuditLog(Base):
    """审计日志表"""
    __tablename__ = "audit_logs"

    thread_id = Column(String(100), nullable=False, index=True, comment="LangGraph thread_id")
    user_id = Column(String(36), nullable=False, comment="用户ID")
    session_id = Column(String(100), comment="会话ID")

    # 用户输入
    original_query = Column(Text, nullable=False, comment="用户原始自然语言问题")
    query_complexity = Column(String(20), comment="复杂度: simple/medium/complex")
    risk_level = Column(String(20), comment="风险等级: low/medium/high")

    # SQL相关
    generated_sql = Column(Text, comment="LLM生成的原始SQL")
    executed_sql = Column(Text, comment="最终执行的SQL（含注入权限条件）")
    sql_safe = Column(Boolean, comment="SQL安全校验是否通过")
    permission_pass = Column(Boolean, comment="权限校验是否通过")

    # 人工审核
    human_reviewed = Column(Boolean, default=False, comment="是否触发人工审核")
    human_approved = Column(Boolean, comment="审核是否通过")
    reviewer_id = Column(String(36), comment="审核人ID")
    review_comment = Column(Text, comment="审核意见")

    # 执行结果
    execution_success = Column(Boolean, comment="执行是否成功")
    execution_time_ms = Column(Integer, comment="执行耗时（毫秒）")
    row_count = Column(Integer, comment="返回行数")
    error_message = Column(Text, comment="错误信息")
    masked_fields = Column(JSON, comment="被脱敏的字段列表")

    # 工具调用追踪
    tool_name = Column(String(100), comment="工具名称")
    tool_input = Column(Text, comment="工具输入（截断）")
    tool_output_summary = Column(Text, comment="工具输出摘要")

    # Critic 评估
    critic_score = Column(Float, comment="Critic 质量评分")
    critic_result = Column(JSON, comment="Critic 溯源结果")

    def __repr__(self):
        return f"<AuditLog(id={self.id}, user_id={self.user_id}, thread_id={self.thread_id})>"