from llama_index.core.base.llms.types import CompletionResponse
from openai import AsyncOpenAI


class OpenAICompatibleChatClient:
    """Small async chat adapter for OpenAI-compatible cloud endpoints."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def acomplete(self, prompt: str) -> CompletionResponse:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        return CompletionResponse(text=content)
