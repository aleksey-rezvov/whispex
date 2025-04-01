import logging
import sys

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Create global logger object
log = logging.getLogger("whispex")

# Don't propagate to root logger to avoid duplicate messages
# log.propagate = False
