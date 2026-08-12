import logging
from typing import Optional

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class LLMService:
    """Service to interact with the Nemotron 3 Ultra LLM."""

    def __init__(self):
        self.api_key = settings.llm_api_key
        self.base_url = settings.llm_base_url
        self.model_name = settings.llm_model_name
        
        # Initialize OpenAI client if API key is provided
        if self.api_key:
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        else:
            self.client = None
            logger.warning("LLM_API_KEY is not set. LLM features will not work.")

    async def analyze_text(self, text: str, prompt: str) -> Optional[str]:
        """
        Analyze the given text based on the provided prompt using the LLM.
        """
        if not self.client:
            logger.error("Attempted to use LLM service without an API key.")
            return "Error: LLM service is not configured (missing API key)."
            
        system_instruction = (
            "You are an AI assistant specialized in analyzing text extracted via OCR. "
            "Please follow the user's instructions exactly."
        )
        
        user_message = f"Here is the extracted text:\n\n{text}\n\nUser Instructions:\n{prompt}"
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.3,
                max_tokens=1024,
            )
            
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error during LLM analysis: {e}")
            return f"Error during analysis: {str(e)}"
