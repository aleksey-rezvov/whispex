import logging
import os
import sys

# Get log level from environment or use INFO as default
log_level_name = os.environ.get("WHISPEX_LOG_LEVEL", "INFO")
log_level = getattr(logging, log_level_name, logging.INFO)

# Configure root logger
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Create global logger object
log = logging.getLogger("whispex")
log.setLevel(log_level)
