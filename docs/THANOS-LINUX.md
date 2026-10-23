# THANOS AMC controller with SimHub on Linux

> **Optional example.** SC-Motion-Haptic-Bridge works with any motion platform SimHub supports. This page is only for the THANOS AMC. Other hardware: [INSTALL-LINUX.md](INSTALL-LINUX.md), section 4.

Since kit 1.3 the THANOS setup lives in its own repository, **simhub-thanos-linux**. It has nothing to do with Star Citizen, so anyone can use it for a THANOS (or any USB serial controller) in SimHub under Wine:

**https://github.com/DMUSER15/simhub-thanos-linux**

It covers the guided SimHub install, the udev permissions, a fixed COM port in SimHub's Wine prefix, the controller file to import in SimHub, Wine's automatic COM ports, the start order and what to do when SimHub hangs. Its own `docs/THANOS.md` and `docs/SERIAL-PORTS.md` replace the instructions that used to be on this page.

## Install and check

```fish
git clone https://github.com/DMUSER15/simhub-thanos-linux ~/simhub-thanos-linux
python3 ~/simhub-thanos-linux/simhub_thanos.py plan      # read-only
python3 ~/simhub-thanos-linux/simhub_thanos.py apply     # SimHub closed, cabinet OFF
```

## How this kit uses it

- Choose `thanos` as the motion hardware: `python3 setup/linux/sc_motion_install.py motion-hw thanos`.
- Installer step 10 and the verifier's `motion` section then run the tool's read-only `plan`. They are DONE/PASS when it reports `READY`. When the tool isn't installed, step 10 is a TODO and the verifier shows an INFO line with the `git clone` command.
- Neither of them ever changes a COM port. A different tool location can be set with env `SC_THANOS_TOOL` (path to `simhub_thanos.py`).

## What changed for existing setups

- The old tool `setup/linux/sc_thanos_serial.py` and its template were removed in kit 1.3; git history keeps them. Its registry entry and `dosdevices` link stay valid; the new tool reads them as they are.
- Controller files the old tool wrote in `~/sc_haptics/` stay where they are. The new tool writes to `~/simhub-thanos/`, and only when you run its `apply`.

## Start order every time

1. E-stop within reach. Cabinet **OFF**.
2. Start SimHub. `sc_start.py` does this. SimHub connects to the THANOS.
3. Cabinet **ON**. If SimHub shows "Actuator is unpowered", reset the connection.
4. Wait until SimHub shows the controller connected, not stuck on "Starting".
