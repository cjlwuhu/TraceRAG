
# -*- coding: UTF-8 -*-
"""
将用户上传图片转换为适合 RAG 检索的 Markdown 文本描述。

作用：
1. 接收 WebUI 上传的 base64 图片；
2. 调用智谱 GLM 多模态模型；
3. 提取图片中的文字、告警、表格、界面元素、关键词；
4. 返回 Markdown 文本；
5. API 层会把这个 Markdown 和用户问题拼接成增强 query，再进入 BM25 检索。
"""

import requests


VISION_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


def image_to_markdown(
    image_base64: str,
    image_mime: str,
    user_query: str,
    api_key: str,
    model: str = "glm-4.6v-flash",
    timeout: int = 120,
) -> str:
    """
    调用 GLM 视觉模型，将图片转为 Markdown 描述。

    参数：
    image_base64: WebUI 传来的 base64 图片，不包含文件头也可以
    image_mime: 图片类型，例如 image/png、image/jpeg
    user_query: 用户输入的问题
    api_key: 智谱 API Key
    model: 多模态模型，默认 glm-4.6v-flash

    返回：
    Markdown 格式的图片描述
    """

    if not image_base64:
        return ""

    if not image_mime:
        image_mime = "image/png"

    # OpenAI 兼容格式通常使用 data URL。
    # 如果平台不接受 data URL，后面会自动 fallback 到纯 base64。
    data_url = f"data:{image_mime};base64,{image_base64}"

    prompt = f"""
请把用户上传的图片转换成适合 RAG 检索的中文 Markdown 描述。

用户的问题是：
{user_query}

请严格按照下面格式输出：

## 图片内容概述
用 2-4 句话概括图片内容。

## 图片中的文字信息
提取图片中可见的文字、标题、按钮、菜单、报错、告警、参数、编号、表格字段等。
如果没有明显文字，请写“未识别到明显文字”。

## 关键信息
用项目符号列出和用户问题相关的设备名、模块名、故障名、告警名、参数名、操作步骤、页面名称等。

## 适合检索的关键词
给出 5-15 个适合 BM25 检索的中文关键词或英文关键词。

要求：
1. 不要编造图片里没有的信息。
2. 如果看不清，请明确说明“图片不清晰，无法确认”。
3. 尽量保留专业术语、英文缩写、编号和界面字段。
4. 输出必须是 Markdown。
""".strip()

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            VISION_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )

        # 有些平台示例使用纯 base64，而不是 data URL。
        # 如果 data URL 失败，则自动重试纯 base64。
        if resp.status_code >= 400:
            payload["messages"][0]["content"][0]["image_url"]["url"] = image_base64
            resp = requests.post(
                VISION_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )

        resp.raise_for_status()
        data = resp.json()

        return data["choices"][0]["message"]["content"]

    except Exception as e:
        return f"## 图片解析失败\n图片已上传，但视觉模型解析失败：{str(e)}"
