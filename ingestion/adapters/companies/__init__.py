from .capitec import CapitecScraper
from .discovery import DiscoveryScraper
from .nedbank import NedbankScraper
from .sasol import SasolScraper
from .woolworths import WoolworthsScraper

SCRAPER_CLASSES = {
    "nedbank": NedbankScraper,
    "discovery": DiscoveryScraper,
    "sasol": SasolScraper,
    "capitec": CapitecScraper,
    "woolworths": WoolworthsScraper,
}
