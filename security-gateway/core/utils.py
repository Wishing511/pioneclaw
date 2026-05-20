"""安全网关共享工具函数"""


def normalize_llm_url(url: str) -> str:
    """补全 LLM API URL 的 /chat/completions 路径后缀

    用户常配置 base_url 为 http://host:port/v1 形式，
    实际请求需指向 /v1/chat/completions。
    """
    if not url:
        return url
    if not url.endswith("/chat/completions"):
        return url.rstrip("/") + "/chat/completions"
    return url
