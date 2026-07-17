"""Minimal ComfyUI API client — queue a workflow, wait, fetch outputs.

Deps: pip install -e '.[comfyui]'

Workflow pattern:
  1. In ComfyUI, build a graph and use *Save (API Format)* to export JSON.
  2. Save it under tools/workflows/ (or a project's src/).
  3. queue it here, optionally templating inputs (prompt, seed, image path).

This is deliberately thin: HTTP queue + history poll. Add the websocket
progress feed (`/ws?clientId=…`) when you want live progress.
"""

import json
import time
import uuid
from pathlib import Path

import httpx


class ComfyUIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ComfyUIClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @staticmethod
    def load_workflow(path: str | Path) -> dict:
        """Load a workflow exported via ComfyUI's 'Save (API Format)'."""
        return json.loads(Path(path).read_text())

    def queue(self, workflow: dict) -> str:
        """Queue a workflow; returns its prompt_id."""
        resp = self._http.post(
            "/prompt", json={"prompt": workflow, "client_id": self.client_id}
        )
        resp.raise_for_status()
        return resp.json()["prompt_id"]

    def wait(self, prompt_id: str, poll: float = 1.0) -> dict:
        """Poll /history until the prompt finishes; returns its history entry."""
        while True:
            resp = self._http.get(f"/history/{prompt_id}")
            resp.raise_for_status()
            history = resp.json()
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(poll)

    def image_url(self, filename: str, subfolder: str = "", type_: str = "output") -> str:
        return (
            f"{self.base_url}/view?filename={filename}"
            f"&subfolder={subfolder}&type={type_}"
        )

    def run(self, workflow: dict) -> dict:
        """Convenience: queue → wait. Returns the finished history entry."""
        return self.wait(self.queue(workflow))
