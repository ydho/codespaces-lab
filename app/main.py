import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.llm import ask, load_config

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

# 啟動時就驗證設定（fail fast）：設定錯誤時容器直接退出，錯誤訊息出現在 docker logs
CONFIG = load_config()
GIT_SHA = os.getenv("GIT_SHA", "dev")  # 建置時由 CI 寫入，用來證明「線上跑的是哪一版」
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "你是一位樂於助人的助理，請用繁體中文簡潔回答。")

log.info("startup version=%s provider=%s model=%s mode=%s",
         GIT_SHA, CONFIG.provider, CONFIG.model, "mock" if CONFIG.is_mock else "live")  # 絕不記錄金鑰

app = FastAPI(title="AI Assistant Demo")


class AskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """健康檢查：容器、CI 與部署平台都靠它判斷服務是否活著。"""
    return {"status": "ok"}


@app.get("/version")
def version() -> dict[str, str]:
    """版本資訊：回溯（rollback）後用它確認實際執行的版本。"""
    return {"git_sha": GIT_SHA, "provider": CONFIG.provider, "mode": "mock" if CONFIG.is_mock else "live"}


@app.post("/api/ask")
def api_ask(req: AskRequest) -> dict[str, object]:
    r = ask(req.prompt, CONFIG, system=SYSTEM_PROMPT)
    return {"text": r.text, "provider": r.provider, "is_mock": r.is_mock, "reason": r.reason}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html><meta charset="utf-8"><title>AI Assistant Demo</title>
<h1>AI Assistant Demo</h1>
<textarea id="q" rows="3" style="width:100%"></textarea>
<button onclick="send()">送出</button><pre id="a"></pre>
<script>
async function send() {
  const a = document.getElementById('a'); a.textContent = '思考中…';
  try {
    const r = await fetch('/api/ask', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({prompt: document.getElementById('q').value})});
    if (!r.ok) { a.textContent = '請求失敗：HTTP ' + r.status; return; }
    const d = await r.json();
    a.textContent = (d.is_mock ? '[示範模式：' + d.reason + '] ' : '[' + d.provider + '] ') + d.text;
  } catch (e) { a.textContent = '連線失敗：' + e; }
}
</script>"""