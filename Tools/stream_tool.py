# stream_tool.py
import os
from openai import AsyncOpenAI  # 🔥 改为 AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# 🔥 改为 AsyncOpenAI 客户端
client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)


# 🔥 函数改为 async def
async def stream_llm(model: str, prompt: str, writer=None):
    """异步流式调用大模型，强制输出 JSON 格式，并实时通过 writer 推送 token"""
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        response_format={"type": "json_object"}
    )

    full_text = ""
    in_string = False
    escaping = False
    current_key = None
    buffer = ""

    # 🔥 改为 async for 迭代异步生成器
    async for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content

        if delta:
            full_text += delta

            if writer:
                for char in delta:
                    buffer += char
                    if len(buffer) > 30:
                        buffer = buffer[-30:]

                    if escaping:
                        escaping = False
                        if current_key and in_string:
                            writer({"type": f"{current_key}_chunk", "content": char})
                        continue

                    if char == '\\':
                        escaping = True
                        continue

                    if char == '"':
                        in_string = not in_string
                        if in_string:
                            if buffer.endswith('"title":'):
                                current_key = "title"
                            elif buffer.endswith('"body":'):
                                current_key = "body"
                            elif buffer.endswith('"image_prompt":'):
                                current_key = "image_prompt"
                            else:
                                current_key = None
                        else:
                            current_key = None
                        continue

                    if in_string and current_key in ["title", "body"]:
                        writer({"type": f"{current_key}_chunk", "content": char})

            yield delta