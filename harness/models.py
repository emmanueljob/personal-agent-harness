import os


def context_window(config):
    """Use saved turn limits first, then independent provider allowances."""
    if 'context_window' in config:
        return int(config['context_window'])
    if config['provider'] == 'openai':
        return int(os.getenv('OPENAI_CONTEXT', '131072'))
    return int(os.getenv('OLLAMA_CONTEXT', '8192'))


def agent_runtime():
    value=os.getenv('AGENT_RUNTIME','legacy')
    if value not in ('legacy','deepagents'):
        raise ValueError('AGENT_RUNTIME must be legacy or deepagents')
    return value


def qwen_available():
    if os.getenv('MODEL_BRIDGE_TOKEN'):
        from .model_bridge import available
        return available()
    return os.getenv('PUBLIC_ONLY','').lower()!='true' and os.getenv('MODEL_PROVIDER')=='ollama'


def settings(choice='default'):
    provider = os.getenv("MODEL_PROVIDER", "openai")
    if provider not in ("openai", "ollama", "hybrid"):
        raise ValueError("MODEL_PROVIDER must be openai, ollama or hybrid")
    if choice not in ('default','openai','qwen'):
        raise ValueError('Choose the default model, OpenAI or experimental Qwen.')
    bridged = False
    if choice == 'openai':
        provider = 'openai'
    elif choice == 'qwen':
        if not qwen_available():
            raise ValueError('The experimental laptop model is offline. Choose OpenAI for your next message.')
        provider = 'ollama'
        bridged = bool(os.getenv('MODEL_BRIDGE_TOKEN'))
    if provider == 'hybrid':
        from .model_bridge import available
        bridged = available()
        provider = 'ollama' if bridged else 'openai'
    value = {"provider": provider, "model": os.getenv("OLLAMA_MODEL", "qwen3.8:27b") if provider == "ollama" else os.getenv("OPENAI_MODEL", "gpt-5.5"), "configured": provider == "ollama" or bool(os.getenv("OPENAI_API_KEY")), "agent_runtime":agent_runtime()}
    if bridged:
        value.update(bridged=True, base_url='http://127.0.0.1:8080/api/model-bridge')
    return value


def local_model(config=None):
    from langchain_ollama import ChatOllama
    config=config or settings()
    client = {"timeout": 280}
    if config.get('bridged'):
        client['headers'] = {'Authorization':'Bearer '+os.environ['MODEL_BRIDGE_TOKEN']}
    return ChatOllama(model=config["model"], base_url=config.get('base_url',os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")), temperature=0.3, num_ctx=int(config.get('context_window',os.getenv("OLLAMA_CONTEXT", "8192"))), num_predict=2048, reasoning=False, client_kwargs=client)


def openai_options(config=None):
    config = config or settings()
    options = dict(model=config['model'], timeout=90, max_retries=0 if config.get('public_deployment') else 2)
    limit = config.get('max_output_tokens')
    if limit is not None:
        options['max_completion_tokens'] = int(limit)
    effort = config.get('reasoning_effort')
    if effort:
        options['reasoning_effort'] = effort
    return options
