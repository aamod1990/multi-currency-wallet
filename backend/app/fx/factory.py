import httpx

from app.core.config import Settings
from app.fx.providers import (
    ChainedProvider,
    FrankfurterProvider,
    FxProvider,
    OpenErApiProvider,
    StaticProvider,
)


def build_provider(settings: Settings) -> FxProvider:
    if settings.fx_provider == "static":
        return StaticProvider()
    client = httpx.Client(
        timeout=settings.fx_http_timeout_seconds,
        headers={"User-Agent": f"{settings.app_name}/{settings.version}"},
    )
    return ChainedProvider([OpenErApiProvider(client), FrankfurterProvider(client)])
