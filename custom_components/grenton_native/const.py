"""Constants for the Grenton Native integration."""

DOMAIN = "grenton_native"

# config entry keys
CONF_OMP_PATH = "omp_path"
CONF_REPORT_PORT_BASE = "report_port_base"
CONF_CHECKALIVE_INTERVAL = "checkalive_interval"
CONF_SUBSCRIBE = "subscribe"
CONF_INDICES = "indices"

# defaults
DEFAULT_REPORT_PORT_BASE = 4344
DEFAULT_CHECKALIVE_INTERVAL = 30  # seconds
DEFAULT_INDICES = "0-15"
EVENT_RING_SIZE = 500

# Where an uploaded project is persisted (under the HA config dir), so it
# survives restarts and is re-read without re-uploading.
PROJECT_FILENAME = "project.omp"

# Persisted runtime state (e.g. the connect/disconnect kill-switch), so a manual
# "disconnect" survives a Home Assistant restart.
STATE_FILENAME = "runtime.json"
