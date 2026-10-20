# test_redis.py
import asyncio
import os
import sys
import traceback
from urllib.parse import quote

# 强制 Windows 使用 SelectorEventLoop
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv

load_dotenv()

import redis.asyncio as redis

# 从 .env 读取，不在代码里硬编码凭据
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))


async def test_method_1():
    """方式1：传 URL（带密码）"""
    print("\n--- 正在测试方式1: URL 带密码 ---")
    auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
    url = f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    client = redis.from_url(url, decode_responses=True)
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=2.0)
        print(f"✅ 方式1成功: {pong}")
    except Exception as e:
        print(f"❌ 方式1失败: {type(e).__name__} - {e}")
    finally:
        await client.close()


async def test_method_2():
    """方式2：拆分参数（带密码）"""
    print("\n--- 正在测试方式2: 拆分参数 带密码 ---")
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
        decode_responses=True
    )
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=2.0)
        print(f"✅ 方式2成功: {pong}")
    except Exception as e:
        print(f"❌ 方式2失败: {type(e).__name__} - {e}")
    finally:
        await client.close()


async def test_method_3():
    """方式3：无密码连接（测试是否密码搞错了）"""
    print("\n--- 正在测试方式3: 无密码连接 ---")
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=None,
        db=REDIS_DB,
        decode_responses=True
    )
    try:
        pong = await asyncio.wait_for(client.ping(), timeout=2.0)
        print(f"✅ 方式3成功: {pong}")
    except Exception as e:
        print(f"❌ 方式3失败: {type(e).__name__} - {e}")
    finally:
        await client.close()

async def main():
    await test_method_1()
    await test_method_2()
    await test_method_3()

if __name__ == "__main__":
    asyncio.run(main())