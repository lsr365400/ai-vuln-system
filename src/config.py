from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # DeepSeek API
    deepseek_api_key: str = ''
    deepseek_base_url: str = 'https://api.deepseek.com'
    deepseek_model: str = 'deepseek-v4-pro'

    # SiliconFlow Embedding API
    embedding_api_key: str = ''
    embedding_api_url: str = 'https://api.siliconflow.cn/v1/embeddings'
    embedding_model: str = 'BAAI/bge-large-zh-v1.5'

    # Auth
    auth_password: str = ''
    auth_secret: str = 'change-me-to-a-random-string'

    # Project paths
    project_root: Path = Path('.')
    skill_file: Path = Path('skills/core-skill.md')
    report_dir: Path = Path('data/reports')
    session_dir: Path = Path('data/sessions')

    # Database
    database_path: Path = Path('data/db.sqlite3')

    # Safety
    session_disk_limit_gb: int = 5
    session_max_turns: int = 200
    session_timeout_hours: int = 4

    def resolve_paths(self):
        root = self.project_root.resolve()
        self.skill_file = root / self.skill_file
        self.report_dir = root / self.report_dir
        self.session_dir = root / self.session_dir
        self.database_path = root / self.database_path
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    s = Settings()
    s.resolve_paths()
    return s
