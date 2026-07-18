"""NVIDIA AI API client for MindMirror AI."""

import httpx
import logging
from typing import Dict, Any, Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class NVIDIAAIClient:
    """Client for NVIDIA AI API."""
    
    def __init__(self):
        self.api_key = settings.nvidia_api_key
        self.base_url = "https://integrate.api.nvidia.com/v1"
        self.timeout = 60.0
    
    async def chat_completion(
        self, 
        messages: list[dict[str, str]], 
        model: str = "meta/llama-3.1-405b-instruct",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Optional[str]:
        """Send chat completion request to NVIDIA AI API."""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error(f"NVIDIA API HTTP error: {e.response.status_code} - {e.response.text}")
            return None
        except httpx.RequestError as e:
            logger.error(f"NVIDIA API request error: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling NVIDIA API: {str(e)}")
            return None


# Singleton instance
_nvidia_client: Optional[NVIDIAAIClient] = None


def get_nvidia_client() -> NVIDIAAIClient:
    """Get or create NVIDIA AI client singleton."""
    global _nvidia_client
    if _nvidia_client is None:
        _nvidia_client = NVIDIAAIClient()
    return _nvidia_client
