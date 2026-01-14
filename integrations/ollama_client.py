"""
Ollama client for Phi AI chat interface.
Supports streaming and non-streaming responses.
"""
import os
import requests
from typing import Iterator, Optional, Dict, Any
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=True)
except ImportError:
    pass

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


def check_ollama_health():
    """
    Check if Ollama is running and accessible.
    Returns (is_healthy, error_message) tuple
    """
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            return True, None
        else:
            return False, f"Ollama returned status {response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to Ollama. Make sure Ollama is running (run 'ollama serve')."
    except requests.exceptions.Timeout:
        return False, "Ollama connection timed out."
    except Exception as e:
        return False, f"Error checking Ollama: {str(e)}"


def check_model_available():
    """
    Check if the configured model is available.
    Returns (is_available, error_message) tuple
    """
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code != 200:
            return False, "Cannot connect to Ollama."
        
        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        
        if OLLAMA_MODEL not in model_names:
            return False, f"Model '{OLLAMA_MODEL}' not found. Install it with: ollama pull {OLLAMA_MODEL}"
        
        return True, None
    except Exception as e:
        return False, f"Error checking model: {str(e)}"


def generate_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    context: Optional[str] = None,
    stream: bool = True
) -> Iterator[str]:
    """
    Generate a response from Ollama.
    
    Args:
        prompt: User's message
        system_prompt: System instructions for the model
        context: Additional context (e.g., meeting summaries)
        stream: Whether to stream the response
    
    Yields:
        Token strings as they're generated
    """
    full_prompt = prompt
    if context:
        full_prompt = f"{context}\n\nUser: {prompt}\n\nAssistant:"
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": full_prompt,
        "stream": stream,
        "options": {
            "temperature": 0.7,
            "top_p": 0.9,
        }
    }
    
    if system_prompt:
        payload["system"] = system_prompt
    
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            stream=stream,
            timeout=300
        )
        
        if response.status_code != 200:
            error_msg = response.text or f"Ollama returned status {response.status_code}"
            yield f"Error: {error_msg}"
            return
        
        if stream:
            for line in response.iter_lines():
                if line:
                    try:
                        data = line.decode('utf-8')
                        if data.strip():
                            import json
                            chunk = json.loads(data)
                            if "response" in chunk:
                                yield chunk["response"]
                            if chunk.get("done", False):
                                break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        else:
            result = response.json()
            yield result.get("response", "")
    
    except requests.exceptions.Timeout:
        yield "Error: Request timed out. The model may be taking too long to respond."
    except Exception as e:
        yield f"Error: {str(e)}"


def generate_conversational_response(
    message: str,
    system_prompt: Optional[str] = None,
    context: Optional[str] = None,
    conversation_history: Optional[list] = None,
    stream: bool = True
) -> Iterator[str]:
    """
    Generate a conversational response with memory of previous messages.
    This creates a more natural, ChatGPT-like conversation experience.
    
    Args:
        message: Current user message
        system_prompt: System instructions for the model
        context: Additional context (e.g., meeting data)
        conversation_history: List of previous messages [{"role": "user/assistant", "content": "..."}]
        stream: Whether to stream the response
    
    Yields:
        Token strings as they're generated
    """
    # Build conversation prompt
    conversation_parts = []
    
    # Add context if provided
    if context:
        conversation_parts.append(context)
    
    # Add conversation history
    if conversation_history:
        for msg in conversation_history:
            role = "User" if msg["role"] == "user" else "Assistant"
            conversation_parts.append(f"{role}: {msg['content']}")
    
    # Add current message
    conversation_parts.append(f"User: {message}")
    conversation_parts.append("Assistant:")
    
    full_prompt = "\n\n".join(conversation_parts)
    
    # Enhanced parameters for better conversation
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": full_prompt,
        "stream": stream,
        "options": {
            "temperature": 0.8,  # Slightly higher for more natural conversation
            "top_p": 0.95,  # Higher for more diverse responses
            "top_k": 40,  # Better token selection
            "repeat_penalty": 1.1,  # Reduce repetition
            "num_predict": 2048,  # Allow longer responses
        }
    }
    
    if system_prompt:
        payload["system"] = system_prompt
    
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            stream=stream,
            timeout=300
        )
        
        if response.status_code != 200:
            error_msg = response.text or f"Ollama returned status {response.status_code}"
            yield f"Error: {error_msg}"
            return
        
        if stream:
            for line in response.iter_lines():
                if line:
                    try:
                        data = line.decode('utf-8')
                        if data.strip():
                            import json
                            chunk = json.loads(data)
                            if "response" in chunk:
                                yield chunk["response"]
                            if chunk.get("done", False):
                                break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
        else:
            result = response.json()
            yield result.get("response", "")
    
    except requests.exceptions.Timeout:
        yield "Error: Request timed out. The model may be taking too long to respond."
    except Exception as e:
        yield f"Error: {str(e)}"


def generate_response_non_streaming(
    prompt: str,
    system_prompt: Optional[str] = None,
    context: Optional[str] = None
) -> str:
    """
    Generate a non-streaming response (for fallback).
    """
    response_text = ""
    for chunk in generate_response(prompt, system_prompt, context, stream=False):
        response_text += chunk
    return response_text
