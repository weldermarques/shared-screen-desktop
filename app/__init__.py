try:
    from ._build_config import VERSION as __version__
except ImportError:  # rodando do código-fonte
    __version__ = "0.0.0-dev"

APP_NAME = "Shared Screen"
