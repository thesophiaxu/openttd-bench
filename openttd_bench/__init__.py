"""OpenTTD bench v3: a multi-agent OpenTTD benchmark on the verifiers framework.

The package exports its taskset and its env (the plugin loader reads `__all__`)."""

from openttd_bench.env import OpenTTDEnv
from openttd_bench.taskset import OpenTTDTaskset

__all__ = ["OpenTTDTaskset", "OpenTTDEnv"]
