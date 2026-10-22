# Adaptive Temperature

Predictive nozzle temperature scheduling for **OrcaSlicer 2.5 nightly**. Use one filament profile across changing flow demand, with a temperature/flow curve and a measured heating and cooling response for each hotend.

**[Download 0.5.8](dist/Adaptive-Temperature-0.5.8.zip?raw=1)** · **[Read the illustrated guide](docs/USER_GUIDE.md)** · **[Download the PDF](docs/Adaptive%20Temperature%20Guide/Adaptive%20Temperature%20User%20Guide.pdf?raw=1)**

![Adaptive Temperature interface showing temperature and flow settings](docs/Adaptive%20Temperature%20Guide/images/02-curve.png)

*Example UI values are illustrative. Enter reliable flow limits for your own filament, hotend and nozzle.*

## What it does

- Schedules temperature increases before higher flow demand and anticipates cooling as demand falls.
- Uses speed, acceleration and a calibrated nozzle sensor response to forecast upcoming demand.
- Supports a single tool or up to four toolchanger profiles, with calibration copying for identical setups.
- Provides Moonraker/Klipper calibration, hotend geometry presets and adjustable burst scheduling.
- Inserts tool-specific `M104` commands into exported G-code while preserving original startup, first-layer and shutdown commands.

## Requirements

| Component | Support |
| --- | --- |
| Slicer | OrcaSlicer 2.5 nightly with Pages and Slicing Pipeline plugin APIs; checked against nightly `24571737` |
| Plugin Python | Python 3.12 or newer |
| Live calibration | Windows with Moonraker/Klipper |
| Filament diameter | Current live runner and editor support 1.75 mm |
| Output | G-code file export; adaptive output currently blocks direct upload |

This is an experimental integration. The model estimates commanded flow and nozzle sensor response; verify the exported output and print quality on your machine.

## Install and start

1. Download and extract the [current release bundle](dist/Adaptive-Temperature-0.5.8.zip?raw=1), or download the [wheel directly](dist/Adaptive%20Temperature%200.5.8/adaptive_temperature-0.5.8-py3-none-any.whl?raw=1).
2. Install the `.whl` through Orca's plugin manager and enable its **Adaptive Temperature** Pages capability. Open the tab beside **Project**.
3. In **Process → Others → Slicing Pipeline Plugin**, choose **+ Add plugin → Adaptive Temperature — slice analysis**. Use Advanced settings mode if the option is hidden.
4. Configure the selected tool, its reliable temperature/flow curve and operating bounds. Start in **Analyze only**.
5. Run the heating/cooling calibration, choose **Use latest recorded response**, then **Save output settings**. Follow the [illustrated calibration instructions](docs/USER_GUIDE.md#response).
6. Select **Adaptive temperature · insert M104 commands**, save, then **export G-code to a file**.
7. Open the exported file in Orca's G-code viewer and select **Temperature** to inspect the scheduled setpoints.

The existing slice Preview does not refresh after post-processing. Use the exported file to inspect the changes.

## Learn the controls

The **[illustrated user guide](docs/USER_GUIDE.md)** is readable directly on GitHub, with all ten UI pictures, input explanations, calibration steps, multi-tool setup and troubleshooting. It also includes practical tips for building a curve from known filament presets.

- [Printable PDF](docs/Adaptive%20Temperature%20Guide/Adaptive%20Temperature%20User%20Guide.pdf?raw=1)
- [Offline guide bundle](dist/Adaptive-Temperature-0.5.8-User-Guide.zip?raw=1)
- [Detailed reference](README.txt)
- [Changelog](CHANGELOG.txt)
- [Release verification](RELEASE_CHECKS.txt)

## Development and updates

Work from a Git clone of the repository. Keep source changes, generated guide updates and release artifacts in the same reviewed commit when preparing a release. Windows CI runs the regression suite, documentation checks and package/privacy checks on pushes and pull requests.

```text
python -B -m unittest discover -s . -p "test_*.py" -v
python tools/build_repo_docs.py --check
python tools/check_public_release.py
```

See **[Contributing](CONTRIBUTING.md)** for the branch, test and push workflow, or [DEVELOPMENT.txt](DEVELOPMENT.txt) for the module layout and detailed tooling. Local printer settings, telemetry, browser profiles and build scratch files are excluded from Git.

## License and credits

Plugin code is **GPL-3.0-only**; see [LICENSE](LICENSE). Motion planning portions adapt Klipper code by Kevin O'Connor; attribution is retained in [LICENSE.txt](LICENSE.txt).

Bundled hotend reference data is **CC BY 4.0**, by Robert Samples / MeltCalc. See the [data attribution](plugin/orca_flow/resources/meltcalc/ATTRIBUTION.txt) and [data license](plugin/orca_flow/resources/meltcalc/LICENSE-CONTENT.txt). The burst scheduling heuristic is part of Adaptive Temperature.
