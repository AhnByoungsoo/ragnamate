from services.price_provider.base import PriceProvider
from services.price_provider.local import LocalPriceProvider
from services.price_provider.mock import MockPriceProvider

PROVIDERS = {p.key: p for p in (LocalPriceProvider, MockPriceProvider)}


def create_provider(key, db, settings):
    return PROVIDERS.get(key, LocalPriceProvider)(db, settings)
