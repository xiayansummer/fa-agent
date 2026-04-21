import json
from pathlib import Path

class PromptRegistry:
    def __init__(self, base_dir: str = "prompts"):
        self.base_dir = Path(base_dir)

    def _prompt_path(self, name: str) -> Path:
        """orchestrator.intent_routing → prompts/orchestrator/intent_routing/"""
        parts = name.split(".")
        return self.base_dir.joinpath(*parts)

    def _find_active_version(self, prompt_dir: Path) -> str:
        """扫描目录，找到 status=active 的版本号"""
        for meta_file in sorted(prompt_dir.glob("*.meta.json")):
            meta = json.loads(meta_file.read_text())
            if meta.get("status") == "active":
                return meta["version"]
        raise FileNotFoundError(f"No active prompt version in {prompt_dir}")

    def get(self, name: str, variables: dict = {}, version: str = "current") -> str:
        prompt_dir = self._prompt_path(name)
        if not prompt_dir.exists():
            raise FileNotFoundError(f"Prompt not found: {name}")

        if version == "current":
            version = self._find_active_version(prompt_dir)

        txt_file = prompt_dir / f"{version}.txt"
        if not txt_file.exists():
            raise FileNotFoundError(f"Prompt version {version} not found for {name}")

        template = txt_file.read_text(encoding="utf-8")
        if variables:
            template = template.format(**variables)
        return template

    def get_meta(self, name: str, version: str = "current") -> dict:
        prompt_dir = self._prompt_path(name)
        if version == "current":
            version = self._find_active_version(prompt_dir)
        meta_file = prompt_dir / f"{version}.meta.json"
        return json.loads(meta_file.read_text())

# 全局单例
registry = PromptRegistry(base_dir="prompts")
