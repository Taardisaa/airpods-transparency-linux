# Third-party attribution

Protocol layout and behavioral implementation are derived from LibrePods:
https://github.com/librepods-org/librepods

Reference revision: 89356c08cea3db0b6b54792d9329d09a87cc85fa

Copyright (C) 2025 LibrePods contributors.
Licensed under GNU GPL version 3 or (at your option) any later version.
This project uses the same license. The full license is in LICENSE.

Relevant references include Transparency.kt, ATTManager.kt, the transparency
settings UI, and the Linux documentation and hearing-aid-adjustments.py. This
project rewrites the transport and UI and uses the transparency handle for sound adjustments.

Version 0.2 also implements AACP using the same revision's
`docs/AAP Definitions.md` (L2CAP PSM 0x1001 handshake, feature negotiation,
notifications, battery, metadata, ear state, and listening-mode packets).
The implementation is independently written from the GPL protocol documentation.

PySide6 is an optional runtime dependency distributed under its own licenses.
It is not vendored in this repository.
