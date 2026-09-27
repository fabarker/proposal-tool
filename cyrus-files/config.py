"""
Configuration for the PMG Service.
"""
import os
from pydantic_settings import BaseSettings


class PMGServiceSettings(BaseSettings):
    """Settings for the PMG Service."""

    # Server settings
    host: str = "0.0.0.0"
    port: int = os.getenv("PMG_SVC_PORT",8002)
    workers: int = 4
    reload: bool = False
    debug: bool = True

    # Logging
    log_level: str = "INFO"

    # API settings
    api_prefix: str = "/api/v1"

    # — Versioning ————————————————————————————————————————————————————————
    # Overridable at deploy time via PMG_SVC_PMG_OPTIMIZATION_VERSION.
    pmg_optimization_version: str = os.getenv("PMG_SVC_PMG_OPTIMIZATION_VERSION", "0.1")

    class Config:
        env_prefix = "PMG_SVC_"
        case_sensitive = False


# Singleton instance
settings = PMGServiceSettings()
