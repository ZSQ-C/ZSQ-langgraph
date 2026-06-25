"""
测试数据填充脚本 v3.0

填充初始数据：角色权限、用户、Schema元数据、文档切片

用法:
    python scripts/seed_data.py              # 填充所有数据
    python scripts/seed_data.py --reset      # 清空后重新填充
    python scripts/seed_data.py --users-only # 仅填充用户和角色
"""

import asyncio
import sys
import uuid
from argparse import ArgumentParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from src.db.database import admin_session


# ============================================================
# 预定义 UUID (方便测试引用)
# ============================================================

ROLE_ADMIN_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
ROLE_ANALYST_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
ROLE_VIEWER_ID = uuid.UUID("10000000-0000-0000-0000-000000000003")

USER_ADMIN_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
USER_ANALYST_ID = uuid.UUID("20000000-0000-0000-0000-000000000002")
USER_VIEWER_ID = uuid.UUID("20000000-0000-0000-0000-000000000003")


async def seed_roles(session):
    """填充角色和权限数据."""
    roles = [
        (
            str(ROLE_ADMIN_ID),
            "admin",
            {
                "sales": ["read", "write"],
                "orders": ["read", "write"],
                "servers": ["read", "write"],
                "tickets": ["read", "write", "delete"],
                "employees": ["read"],
                "schema_metadata": ["read"],
                "documents": ["read", "write", "delete"],
            },
            {
                "sales": ["*"],
                "orders": ["*"],
                "servers": ["*"],
                "tickets": ["*"],
                "employees": ["*"],
            },
            {},
            ["*"],
            True,
            5000,
        ),
        (
            str(ROLE_ANALYST_ID),
            "analyst",
            {
                "sales": ["read"],
                "orders": ["read"],
                "servers": ["read"],
                "tickets": ["read", "write"],
            },
            {
                "sales": [
                    "product_name", "category", "amount", "quantity",
                    "sale_date", "dept", "salesperson",
                ],
                "orders": [
                    "order_no", "customer_name", "product_line",
                    "amount", "status", "order_date", "dept",
                ],
                "servers": [
                    "hostname", "status", "failure_count",
                    "last_failure", "dept", "os_version",
                ],
                "tickets": [
                    "ticket_id", "title", "description", "status",
                    "priority", "assignee", "created_at", "updated_at",
                ],
            },
            {
                "sales": "dept = '{{user_dept}}'",
                "orders": "dept = '{{user_dept}}'",
            },
            ["server_ops", "sop", "public", "sales_docs"],
            True,
            1000,
        ),
        (
            str(ROLE_VIEWER_ID),
            "viewer",
            {
                "sales": ["read"],
            },
            {
                "sales": ["product_name", "category", "amount", "sale_date"],
            },
            {
                "sales": "dept = '{{user_dept}}'",
            },
            ["public"],
            False,
            100,
        ),
    ]

    for role_args in roles:
        await session.execute(
            text("""
                INSERT INTO roles (id, role_name, table_permissions, field_permissions,
                                   row_conditions, doc_tags_allowed, can_export, max_query_rows,
                                   create_time, update_time, is_deleted)
                VALUES (:id, :role_name, :table_permissions::jsonb, :field_permissions::jsonb,
                        :row_conditions::jsonb, :doc_tags_allowed, :can_export, :max_query_rows,
                        now(), now(), false)
                ON CONFLICT (role_name) DO UPDATE SET
                    table_permissions = EXCLUDED.table_permissions,
                    field_permissions = EXCLUDED.field_permissions,
                    row_conditions = EXCLUDED.row_conditions,
                    doc_tags_allowed = EXCLUDED.doc_tags_allowed,
                    can_export = EXCLUDED.can_export,
                    max_query_rows = EXCLUDED.max_query_rows,
                    update_time = now()
            """),
            {
                "id": role_args[0],
                "role_name": role_args[1],
                "table_permissions": __import__("json").dumps(role_args[2]),
                "field_permissions": __import__("json").dumps(role_args[3]),
                "row_conditions": __import__("json").dumps(role_args[4]),
                "doc_tags_allowed": role_args[5],
                "can_export": role_args[6],
                "max_query_rows": role_args[7],
            },
        )

    print(f"  [OK] 角色: admin, analyst, viewer")


async def seed_users(session):
    """填充示例用户."""
    import hashlib

    # 简单的密码哈希 (生产环境应用 bcrypt/passlib)
    def hash_pw(pw):
        return hashlib.sha256(pw.encode()).hexdigest()

    users = [
        (
            str(USER_ADMIN_ID),
            "admin",
            hash_pw("admin123"),
            "it_dept",
            str(ROLE_ADMIN_ID),
            True,
        ),
        (
            str(USER_ANALYST_ID),
            "analyst",
            hash_pw("analyst123"),
            "sales_dept",
            str(ROLE_ANALYST_ID),
            True,
        ),
        (
            str(USER_VIEWER_ID),
            "viewer",
            hash_pw("viewer123"),
            "sales_dept",
            str(ROLE_VIEWER_ID),
            True,
        ),
    ]

    for user_args in users:
        await session.execute(
            text("""
                INSERT INTO users (id, username, password_hash, dept, role_id,
                                   is_active, create_time, update_time, is_deleted)
                VALUES (:id, :username, :password_hash, :dept, :role_id,
                        :is_active, now(), now(), false)
                ON CONFLICT (username) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    dept = EXCLUDED.dept,
                    role_id = EXCLUDED.role_id,
                    update_time = now()
            """),
            {
                "id": user_args[0],
                "username": user_args[1],
                "password_hash": user_args[2],
                "dept": user_args[3],
                "role_id": user_args[4],
                "is_active": user_args[5],
            },
        )

    print(f"  [OK] 用户: admin / analyst / viewer")


async def seed_schema_metadata(session):
    """填充 Schema 元数据 (表/字段语义描述，用于 NL2SQL 语义检索)."""
    entries = [
        # sales 表
        ("sales", "product_name", "VARCHAR", "产品名称", False, "笔记本电脑,手机,办公桌"),
        ("sales", "category", "VARCHAR", "产品类别", False, "电子产品,办公用品,食品"),
        ("sales", "amount", "NUMERIC(10,2)", "销售金额（元）", False, "5999.00,2999.00"),
        ("sales", "quantity", "INTEGER", "销售数量", False, "1,2,3"),
        ("sales", "sale_date", "DATE", "销售日期", False, "2024-01-15"),
        ("sales", "dept", "VARCHAR", "销售部门", False, "sales_dept,online_dept"),
        ("sales", "salesperson", "VARCHAR", "销售人员姓名", False, "张三,李四"),
        ("sales", "region", "VARCHAR", "销售区域（华南/华北/华东/华中/西南/西北）", False, "华南,华东"),
        ("sales", "customer_phone", "VARCHAR", "客户联系电话", True, "13812345678"),
        ("sales", "customer_email", "VARCHAR", "客户邮箱", True, "cust1@example.com"),

        # orders 表
        ("orders", "order_no", "VARCHAR", "订单编号", False, "ORD202401001"),
        ("orders", "customer_name", "VARCHAR", "客户公司名称", False, "甲公司,乙公司"),
        ("orders", "product_line", "VARCHAR", "产品线（电子/办公/食品）", False, "电子,办公"),
        ("orders", "amount", "NUMERIC(12,2)", "订单金额（元）", False, "12000.00,5500.00"),
        ("orders", "status", "VARCHAR", "订单状态（completed/pending/cancelled）", False, "completed,pending"),
        ("orders", "order_date", "TIMESTAMP", "下单时间", False, "2024-01-15 10:30:00"),
        ("orders", "dept", "VARCHAR", "负责部门", False, "sales_dept,online_dept"),

        # servers 表
        ("servers", "hostname", "VARCHAR", "服务器主机名", False, "server-01,server-02"),
        ("servers", "ip_address", "VARCHAR", "IP地址", False, "192.168.1.100"),
        ("servers", "status", "VARCHAR", "运行状态（running/stopped/maintenance）", False, "running,maintenance"),
        ("servers", "failure_count", "INTEGER", "近一年故障次数", False, "3,5,8"),
        ("servers", "last_failure", "DATE", "最近一次故障日期", False, "2024-05-10"),
        ("servers", "dept", "VARCHAR", "归属部门", False, "it_dept,sales_dept"),
        ("servers", "os_version", "VARCHAR", "操作系统版本", False, "Ubuntu 22.04,CentOS 8"),
        ("servers", "cpu_cores", "INTEGER", "CPU核心数", False, "16,32"),
        ("servers", "memory_gb", "INTEGER", "内存大小（GB）", False, "64,128"),
        ("servers", "disk_size_gb", "INTEGER", "磁盘大小（GB）", False, "500,1000"),

        # tickets 表
        ("tickets", "ticket_id", "VARCHAR", "工单编号", False, "TK-2024-001"),
        ("tickets", "title", "VARCHAR", "工单标题", False, "服务器故障维修"),
        ("tickets", "description", "TEXT", "工单详细描述", False, ""),
        ("tickets", "status", "VARCHAR", "工单状态（open/in_progress/resolved/closed）", False, "open,in_progress"),
        ("tickets", "priority", "VARCHAR", "优先级（low/medium/high/critical）", False, "medium,high"),
        ("tickets", "assignee", "VARCHAR", "指派处理人", False, "张三"),
        ("tickets", "created_at", "TIMESTAMP", "创建时间", False, ""),
        ("tickets", "updated_at", "TIMESTAMP", "更新时间", False, ""),
        ("tickets", "server_id", "VARCHAR", "关联服务器ID", False, ""),

        # employees 表
        ("employees", "name", "VARCHAR", "员工姓名", False, "张三,李四"),
        ("employees", "dept", "VARCHAR", "所属部门", False, "sales_dept,tech_dept"),
        ("employees", "position", "VARCHAR", "职位", False, "销售经理,高级工程师"),
        ("employees", "salary", "NUMERIC(10,2)", "薪资", True, "15000,25000"),
        ("employees", "phone", "VARCHAR", "手机号码", True, "13812345678"),
        ("employees", "id_card", "VARCHAR", "身份证号", True, "320102199001011234"),
        ("employees", "hire_date", "DATE", "入职日期", False, "2020-01-01"),
    ]

    for entry in entries:
        await session.execute(
            text("""
                INSERT INTO schema_metadata
                    (table_name, column_name, data_type, description, is_sensitive, sample_values,
                     create_time, update_time, is_deleted)
                VALUES
                    (:table_name, :column_name, :data_type, :description, :is_sensitive, :sample_values,
                     now(), now(), false)
                ON CONFLICT (table_name, column_name) DO UPDATE SET
                    data_type = EXCLUDED.data_type,
                    description = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    sample_values = EXCLUDED.sample_values,
                    update_time = now()
            """),
            {
                "table_name": entry[0],
                "column_name": entry[1],
                "data_type": entry[2],
                "description": entry[3],
                "is_sensitive": entry[4],
                "sample_values": entry[5],
            },
        )

    print(f"  [OK] Schema 元数据: {len(entries)} 条字段描述")


async def seed_business_data(session):
    """填充业务演示数据 (sales, orders, servers, tickets, employees)."""
    # 销售数据
    result = await session.execute(text("SELECT COUNT(*) FROM sales"))
    if result.scalar() > 0:
        print("  [SKIP] 销售表已有数据")
    else:
        await session.execute(text("""
            INSERT INTO sales (product_name, category, amount, quantity, sale_date, dept, region, salesperson, customer_phone, customer_email) VALUES
            ('笔记本电脑', '电子产品', 5999.00, 1, '2024-01-15', 'sales_dept', '华南', '张三', '13812345678', 'cust1@example.com'),
            ('手机', '电子产品', 2999.00, 2, '2024-01-20', 'sales_dept', '华南', '李四', '13987654321', 'cust2@example.com'),
            ('办公桌', '办公用品', 1999.00, 1, '2024-02-01', 'sales_dept', '华南', '张三', '13711112222', 'cust3@example.com'),
            ('打印机', '办公用品', 3500.00, 1, '2024-02-10', 'online_dept', '华东', '王五', '13633334444', 'cust4@example.com'),
            ('平板电脑', '电子产品', 4200.00, 3, '2024-02-15', 'online_dept', '华北', '王五', '13555556666', 'cust5@example.com'),
            ('零食礼盒', '食品', 199.00, 10, '2024-03-01', 'sales_dept', '华南', '李四', '13477778888', 'cust6@example.com'),
            ('显示器', '电子产品', 2499.00, 2, '2024-03-10', 'sales_dept', '华南', '张三', '13399990000', 'cust7@example.com'),
            ('咖啡机', '办公用品', 899.00, 1, '2024-03-20', 'online_dept', '华东', '王五', '13211112222', 'cust8@example.com'),
            ('服务器硬盘', 'IT设备', 2800.00, 2, '2024-04-05', 'it_dept', '华南', '赵六', '13122223333', 'cust9@example.com'),
            ('交换机', 'IT设备', 4500.00, 1, '2024-04-15', 'it_dept', '华南', '赵六', '13033334444', 'cust10@example.com'),
            -- 去年同期对比数据 (2023 Q2)
            ('笔记本电脑', '电子产品', 5499.00, 1, '2023-04-10', 'sales_dept', '华南', '张三', '13812345678', 'old_cust1@example.com'),
            ('手机', '电子产品', 2799.00, 1, '2023-05-15', 'sales_dept', '华南', '李四', '13987654321', 'old_cust2@example.com'),
            ('显示器', '电子产品', 2199.00, 1, '2023-06-01', 'sales_dept', '华南', '张三', '13399990000', 'old_cust3@example.com'),
            ('办公桌', '办公用品', 1799.00, 2, '2023-04-20', 'sales_dept', '华南', '张三', '13711112222', 'old_cust4@example.com'),
            ('零食礼盒', '食品', 169.00, 8, '2023-05-01', 'sales_dept', '华南', '李四', '13477778888', 'old_cust5@example.com')
        """))
        print("  [OK] 销售数据: 2024年10条 + 2023年5条")

    # 订单数据
    result = await session.execute(text("SELECT COUNT(*) FROM orders"))
    if result.scalar() > 0:
        print("  [SKIP] 订单表已有数据")
    else:
        await session.execute(text("""
            INSERT INTO orders (order_no, customer_name, product_line, amount, status, order_date, dept) VALUES
            ('ORD202401001', '甲公司', '电子', 12000.00, 'completed', '2024-01-15 10:30:00', 'sales_dept'),
            ('ORD202401002', '乙公司', '办公', 5500.00, 'completed', '2024-01-20 14:00:00', 'sales_dept'),
            ('ORD202402001', '丙公司', '电子', 8400.00, 'pending', '2024-02-05 09:00:00', 'online_dept'),
            ('ORD202402002', '甲公司', '食品', 1990.00, 'completed', '2024-02-18 16:30:00', 'sales_dept'),
            ('ORD202403001', '丁公司', '办公', 3398.00, 'pending', '2024-03-10 11:00:00', 'online_dept'),
            ('ORD202404001', '戊公司', '电子', 15600.00, 'completed', '2024-04-01 08:00:00', 'sales_dept'),
            ('ORD202404002', '己公司', 'IT设备', 7300.00, 'completed', '2024-04-20 13:00:00', 'it_dept')
        """))
        print("  [OK] 订单数据: 7条")

    # 服务器数据
    result = await session.execute(text("SELECT COUNT(*) FROM servers"))
    if result.scalar() > 0:
        print("  [SKIP] 服务器表已有数据")
    else:
        await session.execute(text("""
            INSERT INTO servers (hostname, ip_address, status, failure_count, last_failure, dept, os_version, cpu_cores, memory_gb, disk_size_gb) VALUES
            ('server-01', '192.168.1.101', 'running', 5, '2024-05-10', 'it_dept', 'Ubuntu 22.04', 16, 64, 500),
            ('server-02', '192.168.1.102', 'running', 3, '2024-05-15', 'it_dept', 'CentOS 8', 16, 64, 1000),
            ('server-03', '192.168.1.103', 'maintenance', 8, '2024-06-01', 'it_dept', 'Ubuntu 22.04', 32, 128, 1000),
            ('server-04', '192.168.1.104', 'running', 1, '2024-03-20', 'sales_dept', 'Ubuntu 20.04', 8, 32, 500),
            ('server-05', '192.168.1.105', 'stopped', 12, '2024-06-10', 'it_dept', 'CentOS 8', 16, 64, 500)
        """))
        print("  [OK] 服务器数据: 5台")

    # 工单数据
    result = await session.execute(text("SELECT COUNT(*) FROM tickets"))
    if result.scalar() > 0:
        print("  [SKIP] 工单表已有数据")
    else:
        await session.execute(text("""
            INSERT INTO tickets (ticket_id, title, description, status, priority, assignee, created_at, updated_at, server_id) VALUES
            ('TK-2024-001', 'server-03 频繁故障排查', '服务器 server-03 近3月故障8次，需要分析根因', 'in_progress', 'high', '张三', '2024-04-01 09:00:00', '2024-04-02 14:00:00', 'server-03'),
            ('TK-2024-002', 'server-05 宕机恢复', 'server-05 因磁盘满宕机，需恢复并清理日志', 'open', 'critical', '李四', '2024-06-10 15:00:00', '2024-06-10 15:00:00', 'server-05'),
            ('TK-2024-003', '数据库连接池优化', '高峰期数据库连接不足，需优化连接池配置', 'open', 'medium', '赵六', '2024-06-15 10:00:00', '2024-06-15 10:00:00', 'server-01'),
            ('TK-2024-004', 'server-01 内存升级', 'server-01 内存不足导致服务响应慢，需升级至128GB', 'resolved', 'medium', '张三', '2024-05-01 08:00:00', '2024-05-20 16:00:00', 'server-01'),
            ('TK-2024-005', 'server-02 安全补丁更新', '应用最新安全补丁 CVE-2024-0123', 'resolved', 'low', '王五', '2024-03-01 11:00:00', '2024-03-02 09:00:00', 'server-02')
        """))
        print("  [OK] 工单数据: 5条")

    # 员工数据
    result = await session.execute(text("SELECT COUNT(*) FROM employees"))
    if result.scalar() > 0:
        print("  [SKIP] 员工表已有数据")
    else:
        await session.execute(text("""
            INSERT INTO employees (name, dept, position, salary, phone, id_card, hire_date) VALUES
            ('张三', 'sales_dept', '销售经理', 15000, '13812345678', '320102199001011234', '2020-01-01'),
            ('李四', 'sales_dept', '销售专员', 10000, '13987654321', '320102199205151234', '2021-06-15'),
            ('王五', 'online_dept', '电商主管', 18000, '13711112222', '320102198812121234', '2019-03-01'),
            ('赵六', 'it_dept', '高级工程师', 25000, '13633334444', '320102198503031234', '2018-01-01'),
            ('孙七', 'it_dept', '运维工程师', 20000, '13555556666', '320102199308081234', '2022-09-01'),
            ('周八', 'sales_dept', '销售助理', 8000, '13477778888', '320102199711111234', '2023-01-01')
        """))
        print("  [OK] 员工数据: 6条")


async def seed_documents(session):
    """填充示例文档和文档切片."""
    result = await session.execute(text("SELECT COUNT(*) FROM documents"))
    if result.scalar() > 0:
        print("  [SKIP] 文档表已有数据")
        return

    doc_id_sop = uuid.UUID("30000000-0000-0000-0000-000000000001")
    doc_id_perf = uuid.UUID("30000000-0000-0000-0000-000000000002")

    # 文档1: 服务器运维SOP
    await session.execute(
        text("""
            INSERT INTO documents (id, title, file_type, file_path, parse_engine,
                                   page_count, tags, chunk_count, is_parsed,
                                   uploaded_by, create_time, update_time, is_deleted)
            VALUES (:id, :title, :file_type, :file_path, :parse_engine,
                    :page_count, :tags, :chunk_count, :is_parsed,
                    :uploaded_by, now(), now(), false)
        """),
        {
            "id": str(doc_id_sop),
            "title": "服务器运维SOP手册",
            "file_type": "pdf",
            "file_path": "documents/server_ops_sop.pdf",
            "parse_engine": "pymupdf",
            "page_count": 15,
            "tags": ["server_ops", "sop"],
            "chunk_count": 5,
            "is_parsed": True,
            "uploaded_by": str(USER_ADMIN_ID),
        },
    )

    # 文档2: 性能优化指南
    await session.execute(
        text("""
            INSERT INTO documents (id, title, file_type, file_path, parse_engine,
                                   page_count, tags, chunk_count, is_parsed,
                                   uploaded_by, create_time, update_time, is_deleted)
            VALUES (:id, :title, :file_type, :file_path, :parse_engine,
                    :page_count, :tags, :chunk_count, :is_parsed,
                    :uploaded_by, now(), now(), false)
        """),
        {
            "id": str(doc_id_perf),
            "title": "服务器性能优化最佳实践",
            "file_type": "pdf",
            "file_path": "documents/perf_guide.pdf",
            "parse_engine": "pymupdf",
            "page_count": 20,
            "tags": ["performance", "public"],
            "chunk_count": 4,
            "is_parsed": True,
            "uploaded_by": str(USER_ADMIN_ID),
        },
    )

    # 文档切片
    chunks = [
        (
            str(doc_id_sop), 0,
            "## 服务器故障应急响应流程\n\n"
            "当服务器发生故障时，应按照以下流程进行应急响应：\n"
            "1. **确认故障范围**: 确定受影响的服务、用户数量和业务范围\n"
            "2. **检查监控告警**: 查阅 Prometheus/Grafana 监控面板，确认告警时间线和指标异常\n"
            "3. **执行应急预案**: 根据故障类型选择对应的应急预案 (A类: 硬件故障 / B类: 网络故障 / C类: 软件故障)\n"
            "4. **记录故障详情**: 在工单系统记录故障时间、现象、处理过程\n"
            "5. **提交事后分析报告**: 24小时内提交 RCA (Root Cause Analysis) 报告",
            1, "paragraph", "应急响应流程",
            {"tags": ["server_ops", "sop"]},
        ),
        (
            str(doc_id_sop), 1,
            "## 服务器故障分类\n\n"
            "根据历史数据统计，服务器故障主要分为以下四类：\n"
            "- **硬件故障 (35%)**: CPU过热、内存错误、磁盘损坏、电源故障\n"
            "- **网络故障 (25%)**: 交换机端口故障、光模块异常、DNS解析失败、网络拥塞\n"
            "- **软件故障 (30%)**: 应用进程崩溃、内存泄漏、配置错误、依赖库版本冲突\n"
            "- **人为故障 (10%)**: 误操作删除文件、错误配置变更、未授权重启服务",
            3, "paragraph", "故障分类",
            {"tags": ["server_ops", "sop"]},
        ),
        (
            str(doc_id_sop), 2,
            "## 磁盘故障处理预案\n\n"
            "1. 确认磁盘故障类型: SMART错误 / 坏道 / 磁盘满\n"
            "2. 对于磁盘满: 清理旧日志 `find /var/log -name '*.log.*' -mtime +30 -delete`\n"
            "3. 对于坏道: 立即迁移数据到备用磁盘，标记故障磁盘为只读\n"
            "4. 联系IDC更换硬盘，填写硬件更换工单\n"
            "5. 新盘到位后执行 raid 重建，监控进度直到完成",
            7, "paragraph", "磁盘故障处理预案",
            {"tags": ["server_ops", "sop"]},
        ),
        (
            str(doc_id_sop), 3,
            "## 服务器巡检制度\n\n"
            "### 日巡检\n"
            "- 检查 CPU 使用率是否超过 80%\n"
            "- 检查内存使用率是否超过 85%\n"
            "- 检查磁盘使用率是否超过 85%\n"
            "- 检查核心服务进程状态\n\n"
            "### 周巡检\n"
            "- 检查安全补丁更新状态\n"
            "- 检查日志是否有异常错误模式\n"
            "- 检查数据库连接池状态\n"
            "- 检查备份完成状态\n\n"
            "### 月巡检\n"
            "- 进行压力测试评估容量\n"
            "- 更新运维文档\n"
            "- 检查硬件健康状态 (SMART / ECC)\n"
            "- 灾难恢复演练",
            10, "paragraph", "服务器巡检制度",
            {"tags": ["server_ops", "sop"]},
        ),
        (
            str(doc_id_sop), 4,
            "## 监控告警阈值配置建议\n\n"
            "| 监控项 | 警告阈值 | 严重阈值 | 建议操作 |\n"
            "|--------|---------|---------|----------|\n"
            "| CPU使用率 | >80% 持续5分钟 | >95% 持续1分钟 | 扩容/限流 |\n"
            "| 内存使用率 | >85% | >95% | 增加内存/排查泄漏 |\n"
            "| 磁盘使用率 | >80% | >90% | 清理/扩容 |\n"
            "| 磁盘IO等待 | >30% | >50% | SSD升级/优化查询 |\n"
            "| 网络连接数 | >10000 | >20000 | 检查连接泄漏 |\n"
            "| 服务响应时间 | >500ms | >2000ms | 优化/扩容 |",
            14, "paragraph", "监控告警阈值配置",
            {"tags": ["server_ops", "sop"]},
        ),
        # 性能优化文档切片
        (
            str(doc_id_perf), 0,
            "## 服务器性能优化 - 内存篇\n\n"
            "### JVM 堆内存配置\n"
            "- 建议堆内存不超过物理内存的 75%\n"
            "- 初始堆 (-Xms) 和最大堆 (-Xmx) 设置为相同值，避免动态扩展开销\n"
            "- 元空间 (-XX:MaxMetaspaceSize) 建议 256MB-512MB\n\n"
            "### 系统内存优化\n"
            "- 调整 swappiness: `sysctl vm.swappiness=10`\n"
            "- 启用透明大页: `echo always > /sys/kernel/mm/transparent_hugepage/enabled`\n"
            "- 限制文件系统缓存不要过大，避免 OOM Killer 误杀",
            5, "paragraph", "内存优化",
            {"tags": ["performance", "public"]},
        ),
        (
            str(doc_id_perf), 1,
            "## 数据库连接池优化\n\n"
            "### 连接池大小计算公式\n"
            "pool_size = ((core_count * 2) + effective_spindle_count)\n"
            "对于 16 核 SSD 服务器: pool_size = (16*2) + 1 = 33\n\n"
            "### 超时配置建议\n"
            "- connectionTimeout: 30000ms (最大等待时间)\n"
            "- idleTimeout: 600000ms (空闲连接回收)\n"
            "- maxLifetime: 1800000ms (连接最大存活时间)\n"
            "- leakDetectionThreshold: 60000ms (连接泄漏检测)\n\n"
            "### 监控指标\n"
            "- 活跃连接数 / 空闲连接数 / 等待线程数\n"
            "- 连接创建速率 / 连接超时次数",
            8, "paragraph", "数据库连接池优化",
            {"tags": ["performance", "public"]},
        ),
        (
            str(doc_id_perf), 2,
            "## 缓存策略设计\n\n"
            "### 多级缓存架构\n"
            "1. L1: 本地缓存 (Caffeine/Guava)\n"
            "   - 容量: 10000 条\n"
            "   - TTL: 5 分钟\n"
            "2. L2: 分布式缓存 (Redis)\n"
            "   - TTL: 30 分钟\n"
            "   - 序列化: Protobuf/MessagePack\n"
            "3. L3: 数据库 (PostgreSQL)\n\n"
            "### 缓存更新策略\n"
            "- Cache Aside: 读缓存 -> 未命中读DB -> 写缓存\n"
            "- Write Through: 同时更新缓存和DB\n"
            "- Write Behind: 异步批量写回DB",
            12, "paragraph", "缓存策略设计",
            {"tags": ["performance", "public"]},
        ),
        (
            str(doc_id_perf), 3,
            "## 性能压测与容量规划\n\n"
            "### 压测工具链\n"
            "- HTTP: wrk / k6 / JMeter\n"
            "- 数据库: pgbench / sysbench\n"
            "- 全链路: Locust\n\n"
            "### 容量规划公式\n"
            "所需服务器数 = (预估QPS * 单请求CPU时间) / (CPU核心数 * 目标利用率)\n\n"
            "### 扩容触发条件\n"
            "- CPU 利用率 > 70% 持续 30 分钟\n"
            "- 内存使用率 > 80% 持续 15 分钟\n"
            "- P99 响应时间 > 基准值 * 2",
            18, "paragraph", "性能压测与容量规划",
            {"tags": ["performance", "public"]},
        ),
    ]

    for chunk in chunks:
        await session.execute(
            text("""
                INSERT INTO document_chunks
                    (document_id, chunk_index, content, page_number, structure_type,
                     parent_heading, metadata, create_time, update_time, is_deleted)
                VALUES
                    (:document_id, :chunk_index, :content, :page_number, :structure_type,
                     :parent_heading, :metadata::jsonb, now(), now(), false)
            """),
            {
                "document_id": chunk[0],
                "chunk_index": chunk[1],
                "content": chunk[2],
                "page_number": chunk[3],
                "structure_type": chunk[4],
                "parent_heading": chunk[5],
                "metadata": __import__("json").dumps(chunk[6]),
            },
        )

    print(f"  [OK] 文档: 2篇, 切片: {len(chunks)}个")


async def main(reset: bool = False, users_only: bool = False):
    """主入口."""
    print("=" * 60)
    print("  企业智能数据分析平台 - 种子数据填充")
    print("=" * 60)

    async with admin_session() as session:
        if reset:
            print("\n[RESET] 清空所有数据...")
            tables = [
                "document_chunks", "documents", "audit_logs",
                "sales", "orders", "servers", "tickets", "employees",
                "schema_metadata", "users", "roles",
            ]
            for t in tables:
                try:
                    await session.execute(text(f"DELETE FROM {t}"))
                except Exception:
                    pass
            await session.commit()
            print("  [OK] 数据已清空")

        print("\n--- 角色与权限 ---")
        await seed_roles(session)

        print("\n--- 用户 ---")
        await seed_users(session)

        if not users_only:
            print("\n--- Schema 元数据 ---")
            await seed_schema_metadata(session)

            print("\n--- 业务演示数据 ---")
            await seed_business_data(session)

            print("\n--- 文档与切片 ---")
            await seed_documents(session)

        await session.commit()

    print("\n" + "=" * 60)
    print("  种子数据填充完成!")
    print("=" * 60)
    print()
    print("默认测试账号:")
    print("  admin   / admin123    (全权限)")
    print("  analyst / analyst123  (sales_dept 分析员)")
    print("  viewer  / viewer123   (sales_dept 只读)")


if __name__ == "__main__":
    parser = ArgumentParser(description="种子数据填充工具")
    parser.add_argument("--reset", action="store_true", help="清空所有数据后重新填充")
    parser.add_argument("--users-only", action="store_true", help="仅填充用户和角色数据")
    args = parser.parse_args()

    asyncio.run(main(reset=args.reset, users_only=args.users_only))
