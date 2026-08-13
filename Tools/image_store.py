import os

import httpx

# 必须和 server.py 里的目录保持一致！
PROJECT_ROOT = "E:/xt/muti-agent_dm1"
SAVE_DIR = os.path.join(PROJECT_ROOT,"data","images")
os.makedirs(SAVE_DIR, exist_ok=True)

async def download_and_save_image(remote_url: str, thread_id: str) -> str | None:

    # 拼接物理绝对路径：例如 /项目路径/my_data/gallery/123-456.png
    file_path = os.path.join(SAVE_DIR, f"{thread_id}.png")

    # 拼接对外暴露的 URL 路径：必须和 server.py mount 的第一个参数一致
    local_url = f"/images/{thread_id}.png"

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(remote_url)
            if resp.status_code == 200 and resp.content:
                with open(file_path, "wb") as f:
                    f.write(resp.content)
                return local_url  # 返回给数据库存储
            else:
                print(f"⚠️ 图片下载失败 status={resp.status_code}")
                return None
    except Exception as e:
        print(f"⚠️ 图片下载异常: {e}")
        return None