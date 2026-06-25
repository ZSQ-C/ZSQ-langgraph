"""数据库初始化脚本"""
import asyncio, logging
from pathlib import Path
from src.db.database import init_db, admin_engine
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    await init_db()
    logger.info("表结构创建完成")
    sql_path = Path(__file__).parent / "init_postgres.sql"
    async with admin_engine.begin() as conn:
        for statement in sql_path.read_text(encoding="utf-8").split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                await conn.execute(text(stmt))
    logger.info("pgvector扩展和索引创建完成")

if __name__ == "__main__":
    asyncio.run(main())
