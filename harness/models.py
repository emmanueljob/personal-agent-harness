import os


def settings():
    provider = os.getenv("MODEL_PROVIDER", "openai")
    if provider not in ("openai", "ollama"):
        raise ValueError("MODEL_PROVIDER must be openai or ollama")
    return {"provider": provider, "model": os.getenv("OLLAMA_MODEL", "qwen3.8:27b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-5.5"), "configured": provider == "ollama" or bool(os.getenv("OPENAI_API_KEY"))}


def local_model(config=None):
    from langchain_ollama import ChatOllama
    return ChatOllama(model=(config or settings())["model"], base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), temperature=0.3, num_ctx=int(os.getenv("OLLAMA_CONTEXT", "8192")), num_predict=2048, reasoning=False, client_kwargs={"timeout": 300})
