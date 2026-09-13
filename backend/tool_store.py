# -*- coding: utf-8 -*-
"""
tool_store.py —— AI 工具用的数据表（建表 + 查询）

原先这些「模拟数据」直接写死在 AIagent/agents.py 的各个 @tool 函数里，
想加个城市、改条汇率都得动代码。现在统一落到 MySQL：

- 表结构和种子数据集中在下面的 _TOOL_TABLES，一张表一条记录，见名知意。
- init_tool_tables()：建表并「只在表为空时」灌入种子数据，幂等，服务启动时调一次。
  之所以只在空表时灌，是为了让建完表之后手工改过的数据不会被重启覆盖回去。
- 查询函数：find_weather / find_product / find_news / find_stock / find_currencies /
  list_currency_codes，查不到统一返回 None，由调用方（@tool）决定给出什么提示语。

连接与事务复用 db.py 的 MYSQL_CONFIG / db_session()，不重复配置一遍。
"""

import db

# ---------------- 表结构与种子数据 ----------------
# 每张表一条记录：
#   name    表名
#   ddl     建表语句（IF NOT EXISTS，可重复执行）
#   columns 列顺序，灌种子数据时按它拼 INSERT
#   seed    首次灌入的数据（仅当表为空时写入）
_TOOL_TABLES = (
    {
        "name": "tool_weather",
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_weather (
                city        VARCHAR(64)  NOT NULL COMMENT '城市名',
                description VARCHAR(255) NOT NULL COMMENT '天气描述',
                PRIMARY KEY (city)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "columns": ("city", "description"),
        "seed": (
            ("北京", "多云，15-22℃，空气质量良，湿度 45%"),
            ("上海", "晴天，18-25℃，空气质量优，湿度 60%"),
            ("深圳", "小雨，22-28℃，空气质量优，湿度 75%"),
            ("成都", "阴天，16-23℃，空气质量良，湿度 70%"),
            ("杭州", "晴天，17-24℃，空气质量优，湿度 55%"),
            ("广州", "多云，21-29℃，空气质量良，湿度 72%"),
        ),
    },
    {
        "name": "tool_currency",
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_currency (
                code        VARCHAR(8)      NOT NULL COMMENT '货币代码',
                name        VARCHAR(32)     NOT NULL COMMENT '货币中文名',
                rate_to_cny DECIMAL(18, 6)  NOT NULL COMMENT '相对人民币的汇率',
                PRIMARY KEY (code)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "columns": ("code", "name", "rate_to_cny"),
        "seed": (
            ("CNY", "人民币", 1.0),
            ("USD", "美元", 0.14),
            ("EUR", "欧元", 0.13),
            ("GBP", "英镑", 0.11),
            ("JPY", "日元", 20.8),
            ("HKD", "港币", 1.09),
        ),
    },
    {
        "name": "tool_product",
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_product (
                keyword     VARCHAR(64)  NOT NULL COMMENT '搜索关键词',
                description VARCHAR(512) NOT NULL COMMENT '产品信息',
                PRIMARY KEY (keyword)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "columns": ("keyword", "description"),
        "seed": (
            ("手机", "iPhone 15 (¥5999), 小米14 (¥3999), 华为Mate60 (¥6999)"),
            ("笔记本", "MacBook Pro (¥12999), ThinkPad X1 (¥9999), 华为MateBook(¥7999)"),
            ("耳机", "AirPods Pro (¥1999), Sony WH-1000XM5 (¥2499)"),
        ),
    },
    {
        "name": "tool_news",
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_news (
                keyword VARCHAR(64)  NOT NULL COMMENT '搜索关键词',
                summary VARCHAR(512) NOT NULL COMMENT '新闻摘要',
                PRIMARY KEY (keyword)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "columns": ("keyword", "summary"),
        # search_info(category="news") 和 search_news 共用这张表：
        # 两者的数据形状都是「关键词 -> 一条新闻摘要」，没必要各维护一份。
        "seed": (
            ("AI", "1. GPT-5 即将发布 2. AI 芯片市场增长 30% 3. 新AI法规出台"),
            ("科技", "1. 量子计算新突破 2. 6G 技术测试 3. 新能源汽车销量创新高"),
            ("人工智能", "1. 大模型推理成本下降 2. AI 芯片需求持续增长"),
            ("苹果公司", "1. 苹果发布新一代芯片 2. 苹果季度营收超预期"),
            ("特斯拉", "1. 特斯拉新工厂投产 2. 特斯拉下调部分车型售价"),
        ),
    },
    {
        "name": "tool_stock",
        "ddl": """
            CREATE TABLE IF NOT EXISTS tool_stock (
                company VARCHAR(64)  NOT NULL COMMENT '公司名',
                quote   VARCHAR(255) NOT NULL COMMENT '行情描述',
                PRIMARY KEY (company)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        "columns": ("company", "quote"),
        "seed": (
            ("苹果公司", "苹果公司(AAPL) 189.30 美元，涨幅 +1.25%"),
            ("特斯拉", "特斯拉(TSLA) 248.50 美元，跌幅 -0.80%"),
            ("微软", "微软(MSFT) 412.60 美元，涨幅 +0.35%"),
        ),
    },
)


# ---------------- 初始化 ----------------

def init_tool_tables():
    """建表，并在表为空时灌入种子数据（幂等）。服务启动时调用一次。"""
    with db.db_session() as cur:
        for table in _TOOL_TABLES:
            cur.execute(table["ddl"])

            cur.execute(f"SELECT COUNT(*) AS total FROM {table['name']}")
            if cur.fetchone()["total"]:
                # 已有数据：可能是手工改过的，不再覆盖
                continue

            columns = ", ".join(table["columns"])
            placeholders = ", ".join(["%s"] * len(table["columns"]))
            cur.executemany(
                f"INSERT INTO {table['name']} ({columns}) VALUES ({placeholders})",
                table["seed"],
            )


# ---------------- 查询 ----------------
# 下面的 SQL 里，表名/列名全部来自本文件顶部的常量，不含任何外部输入，
# 所以可以安全地用 f-string 拼接；值一律走 %s 占位符参数化。

def _find_text(table: str, key_column: str, key: str, value_column: str) -> str | None:
    """按主键查一个文本字段，查不到返回 None。"""
    with db.db_session() as cur:
        cur.execute(
            f"SELECT {value_column} AS value FROM {table} WHERE {key_column} = %s",
            (key,),
        )
        row = cur.fetchone()
    return row["value"] if row else None


def find_weather(city: str) -> str | None:
    """按城市名查天气描述。"""
    return _find_text("tool_weather", "city", city, "description")


def find_product(keyword: str) -> str | None:
    """按关键词查产品信息。"""
    return _find_text("tool_product", "keyword", keyword, "description")


def find_news(keyword: str) -> str | None:
    """按关键词查新闻摘要（search_info 与 search_news 共用）。"""
    return _find_text("tool_news", "keyword", keyword, "summary")


def find_stock(company: str) -> str | None:
    """按公司名查行情描述。"""
    return _find_text("tool_stock", "company", company, "quote")


def find_currencies(codes) -> dict:
    """批量按货币代码查询，返回 {代码: {"code", "name", "rate_to_cny"}}。

    转币要同时用到源货币和目标货币，一次 SQL 拿完，少一次往返；
    库里没有的代码不会出现在返回的字典里，调用方据此判断是否合法。
    """
    codes = [str(code).upper() for code in codes]
    if not codes:
        return {}

    placeholders = ", ".join(["%s"] * len(codes))
    with db.db_session() as cur:
        cur.execute(
            "SELECT code, name, rate_to_cny FROM tool_currency "
            f"WHERE code IN ({placeholders})",
            codes,
        )
        rows = cur.fetchall()

    return {row["code"]: _currency(row) for row in rows}


def list_currency_codes() -> list:
    """列出库里所有货币代码，用于「不支持的货币类型」这类提示语。"""
    with db.db_session() as cur:
        cur.execute("SELECT code FROM tool_currency ORDER BY code")
        rows = cur.fetchall()
    return [row["code"] for row in rows]


def _currency(row) -> dict:
    """把一行货币记录转成字典。

    rate_to_cny 在库里是 DECIMAL（金额/汇率用定点更准），pymysql 读出来是 Decimal；
    这里统一转成 float，调用方可以直接参与算术运算，不必再关心类型。
    """
    return {
        "code": row["code"],
        "name": row["name"],
        "rate_to_cny": float(row["rate_to_cny"]),
    }
