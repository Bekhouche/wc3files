# A library does not print log lines unless the application asks: loguru
# prints to stderr by default, so reading one SLK table printed an INFO line.
# `from loguru import logger; logger.enable('wc3files')` turns them back on.
from loguru import logger as _logger
_logger.disable('wc3files')
del _logger

# Deliberate re-exports: this package's namespace is the whole public API,
# gathered from each format package. pyflakes reports every star import as
# unused; a literal __all__ would have to list every public name of the modules
# below and be kept in step by hand.
from .version import __version__
from .data import *
from .files import *
from .doo import *
from .imp import *
from .mpq import *
from .w3i import *
from .w3o import *
from .w3r import *
from .w3g import *
from .w3f import *
from .w3x import *
from .wtg import *
from .mdl import *
from .img import *
from .scripts import *
from .mmp import *
from .w3e import *
from .wpm import *
from .shd import *
from .w3c import *
from .w3s import *