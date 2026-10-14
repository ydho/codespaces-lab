"""LLM 客戶端：provider 可切換，執行期失敗自動降級。

錯誤分兩類，處理方式刻意不同：
- 設定錯誤（provider 拼錯、有金鑰卻沒指定 model）：拋出 ConfigError，讓服務啟動即失敗（fail fast）。
  若把設定錯誤也降級成 mock，問題會被靜默掩蓋——服務看似正常，其實從未真正呼叫過模型。
- 執行期錯誤（401 金鑰失效 / 404 模型不存在 / 429 限流 / 網路中斷）：降級為 mock 並回報原因，服務不中斷。

設計背景：2026-07 GitHub Models 退役——端點必須可替換，任何供應商失效時應用程式都不能整個崩潰。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)

# OpenAI 相容端點。新增供應商只需在此加一行。
PRESETS: dict[str, str] = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "ollama": "http://localhost:11434/v1",  # Codespace 內本地執行
}

# 只有本地 ollama 給預設 model。雲端供應商的 model ID 變動頻繁，必須明確指定（從控制台或 ListModels 複製），
# 寫死預設值只會在改版後變成一個「靜默的 404」。
DEFAULT_MODELS: dict[str, str] = {"ollama": "qwen2.5:0.5b"}
KEYLESS_PROVIDERS: frozenset[str] = frozenset({"ollama"})

MOCK_REPLY = "【示範模式】目前未連上 LLM，此為預先準備的範例回應。設定有效的金鑰與模型後即可取得真實回覆。"


class ConfigError(ValueError):
    """設定錯誤：應在啟動時就讓服務失敗，而不是在執行期被降級掩蓋。"""


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: str | None
    api_key: str | None
    merge_system: bool

    @property
    def is_mock(self) -> bool:
        # 雲端供應商沒有金鑰 → 必然走 mock
        return not self.api_key and self.provider not in KEYLESS_PROVIDERS


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    is_mock: bool
    reason: str | None = None  # 降級原因，前端與 log 都會顯示，避免「看起來正常其實是 mock」


def load_config() -> LLMConfig:
    provider = os.getenv("LLM_PROVIDER", "google").strip().lower()
    base_url = os.getenv("LLM_BASE_URL") or PRESETS.get(provider)
    if not base_url:
        raise ConfigError(f"未知的 provider：{provider!r}；可用值為 {sorted(PRESETS)}，或改設 LLM_BASE_URL")

    model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider)

    # LLM_MERGE_SYSTEM：auto（預設）＝ 依模型自動判斷；1 ＝ 一律合併；0 ＝ 不合併
    merge_mode = os.getenv("LLM_MERGE_SYSTEM", "auto").strip().lower()
    if merge_mode not in {"auto", "0", "1"}:
        raise ConfigError(f"LLM_MERGE_SYSTEM 只能是 auto、0 或 1，目前為 {merge_mode!r}")
    if merge_mode == "auto":
        merge_system = (model or "").lower().startswith("gemma")  # Gemma 系列不接受 system 角色
    else:
        merge_system = merge_mode == "1"

    cfg = LLMConfig(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=os.getenv("LLM_API_KEY") or None,
        merge_system=merge_system,
    )
    # 沒有金鑰時一定走 mock，不需要 model；有金鑰卻沒有 model 才是設定錯誤
    if not cfg.is_mock and not cfg.model:
        raise ConfigError(f"provider {provider!r} 需要設定 LLM_MODEL（請從供應商控制台或 ListModels 複製確切的 model ID）")
    return cfg


def _mock(reason: str) -> LLMResult:
    return LLMResult(MOCK_REPLY, provider="mock", is_mock=True, reason=reason)


def ask(prompt: str, cfg: LLMConfig, system: str | None = None, timeout: float = 30.0) -> LLMResult:
    """送出一次對話請求。執行期失敗一律降級為 mock 並附上原因，不向上拋例外。"""
    if cfg.is_mock:
        return _mock("未設定 LLM_API_KEY")

    from openai import APIError, APIStatusError, OpenAI  # 延遲載入：mock 模式不需要這個依賴

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key or "ollama", timeout=timeout, max_retries=2)

    messages: list[dict[str, str]] = []
    if system and cfg.merge_system:
        # Gemma 等不支援 system 角色的模型：把系統提示併入 user 訊息
        messages.append({"role": "user", "content": f"{system}\n\n---\n\n{prompt}"})
    else:
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

    try:
        resp = client.chat.completions.create(model=cfg.model, messages=messages)
    except APIStatusError as exc:  # 伺服器有回應但狀態碼非 2xx（400/401/404/429/5xx）
        log.error("%s 呼叫失敗：HTTP %s %s", cfg.provider, exc.status_code, exc.message)
        return _mock(f"{cfg.provider} 回應 HTTP {exc.status_code}")
    except APIError as exc:  # 連線失敗、逾時等，伺服器沒有回應
        log.error("%s 連線失敗：%s", cfg.provider, type(exc).__name__)
        return _mock(f"{cfg.provider} 連線失敗（{type(exc).__name__}）")

    text = resp.choices[0].message.content if resp.choices else None
    if not text:
        return _mock(f"{cfg.provider} 回傳空白內容")
    return LLMResult(text, provider=cfg.provider, is_mock=False)