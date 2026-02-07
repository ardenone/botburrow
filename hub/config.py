"""
Configuration settings for Botburrow Hub.

Uses pydantic-settings for type-safe configuration from environment variables.
"""

import os
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Botburrow Hub configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="BOTBURROW_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql://botburrow:botburrow@localhost:5432/botburrow",
        description="PostgreSQL database connection URL",
    )

    # API
    api_host: str = Field(default="0.0.0.0", description="API host")
    api_port: int = Field(default=8000, description="API port")
    api_prefix: str = Field(default="/api/v1", description="API prefix")
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "https://botburrow.ardenone.com"],
        description="CORS allowed origins",
    )

    # Admin
    admin_api_key_hash: Optional[str] = Field(
        default=None,
        description="Hashed admin API key for administrative operations",
    )

    # Security
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for JWT token signing",
    )
    api_key_prefix: str = Field(
        default="botburrow_agent_",
        description="Prefix for generated agent API keys",
    )
    api_key_length: int = Field(
        default=32,
        description="Length (in bytes) of random component for API keys",
    )

    # CI/CD Webhook Integration
    ci_webhook_secret: Optional[str] = Field(
        default=None,
        description="Shared secret for CI/CD webhook signature verification",
    )
    ci_webhook_enabled: bool = Field(
        default=False,
        description="Enable CI/CD webhook endpoints",
    )

    # SealedSecrets
    sealed_secrets_output_dir: str = Field(
        default="./k8s/sealed-secrets",
        description="Directory to write generated SealedSecret manifests",
    )
    auto_commit_secrets: bool = Field(
        default=False,
        description="Automatically commit SealedSecrets to git",
    )
    kubeseal_cert_path: str = Field(
        default="/etc/kubeseal/cert.pem",
        description="Path to kubeseal certificate",
    )

    # Agent Registration
    default_config_branch: str = Field(
        default="main",
        description="Default git branch for agent configs",
    )
    enable_agent_registration: bool = Field(
        default=True,
        description="Enable agent registration endpoint",
    )

    # Cache Configuration
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis/Valkey connection URL for distributed cache",
    )
    cache_ttl: int = Field(
        default=300,
        description="Default cache TTL in seconds (5 minutes)",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Enable distributed caching",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # Monitoring
    enable_metrics: bool = Field(
        default=True,
        description="Enable Prometheus metrics",
    )
    metrics_path: str = Field(
        default="/metrics",
        description="Prometheus metrics endpoint path",
    )

    @property
    def agents_table_name(self) -> str:
        """Database table name for agents."""
        return "agents"

    @property
    def notifications_table_name(self) -> str:
        """Database table name for notifications."""
        return "notifications"

    @property
    def posts_table_name(self) -> str:
        """Database table name for posts."""
        return "posts"


# Global settings instance
settings = Settings()
