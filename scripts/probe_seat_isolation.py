#!/usr/bin/env python3
"""Historical same-box isolation probe: deliberately unsupported.

Root agents now occupy distinct Prime VMs. Unix-user/setpriv checks cannot
validate VM isolation and could report a vacuous success on an empty box.
"""

if __name__ == "__main__":
    raise SystemExit("Same-box/Docker seat isolation probes are unsupported; use the Prime VM runtime inventory and isolated-VM tests.")
