from __future__ import annotations

from typing import Any, Literal, overload

from video_security.llm.cloud import CloudClient
from video_security.llm.mlx_serve import MlxServeClient
from video_security.llm.ollama import OllamaClient
from video_security.llm.ollama import OllamaError as OllamaError

LLMClient = OllamaClient | MlxServeClient

PROVIDERS = ("ollama", "mlx-serve")


@overload
def make_llm_client(
    config: Any,
    timeout_s: int | None = None,
    backend: Literal["local"] = "local",
) -> LLMClient: ...


@overload
def make_llm_client(
    config: Any,
    timeout_s: int | None = None,
    backend: Literal["cloud"] = "cloud",
) -> CloudClient: ...


def make_llm_client(
    config: Any,
    timeout_s: int | None = None,
    backend: Literal["local", "cloud"] = "local",
) -> LLMClient | CloudClient:
    from video_security.llm import cloud as _cloud
    from video_security.llm import mlx_serve as _mlx
    from video_security.llm import ollama as _ollama

    if backend == "cloud":
        return _cloud.make_cloud_client(config)
    timeout = timeout_s if timeout_s is not None else config.llm_triage.timeout_s
    if config.llm.provider == "mlx-serve":
        return _mlx.MlxServeClient(
            base_url=config.llm.mlx_url, timeout_s=timeout
        )
    return _ollama.OllamaClient(
        base_url=config.llm.ollama_url, timeout_s=timeout
    )
