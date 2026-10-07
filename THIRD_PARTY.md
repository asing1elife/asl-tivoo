# Third-party reference

`native/tivoo_cmd.m` is adapted from solar2ain/tivoo-control:
https://github.com/solar2ain/tivoo-control

Reference commit: `6f24deaf631820089259177805d1a6a6615f6d7e`.
The upstream README declares the project MIT licensed; that revision has no separate LICENSE file.
The original source has no embedded copyright notice. Retain this attribution with copies.
Local changes require an explicit device address and propagate Bluetooth write errors.

The static image encoder implements the packet/palette format documented by that project.
Pillow is installed separately via requirements.txt and retains its own license.
