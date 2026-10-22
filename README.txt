Adaptive Temperature 0.5.8

Predictive nozzle temperature scheduling for OrcaSlicer.
Use one filament profile across changing flow demand, with a calibrated
temperature/flow curve and heating and cooling response for each hotend.

ILLUSTRATED USER GUIDE

Open docs/Adaptive Temperature Guide/index.html for the step-by-step guide
with UI pictures and input explanations. A printable PDF is in the same folder.
The shareable guide is dist/Adaptive-Temperature-0.5.8-User-Guide.zip.
The current release bundle also includes the printable PDF and changelog.

Feature progression and release fixes: CHANGELOG.txt
Build, test, contribution and public-release instructions: DEVELOPMENT.txt

INSTALLATION

Requires OrcaSlicer 2.5 nightly with Pages and Slicing Pipeline plugin support
and Python 3.12 or newer. Integration has been checked against nightly 24571737.
Nightly plugin APIs can change.

1. Install adaptive_temperature-0.5.8-py3-none-any.whl in Orca's plugin manager.
2. Enable the Pages capability "Adaptive Temperature" to add its main tab.
3. Open Process > Others > Slicing Pipeline Plugin. Click "+ Add plugin"
   and select "Adaptive Temperature — slice analysis" for the print.
   Use Advanced settings mode if the option is hidden.
4. Start in Analyze only mode. Configure your tools and calibration before
   enabling Adaptive temperature output.

When upgrading, export or back up the previous plugin's settings first. Disable
its older pipeline capability so two plugins do not process the same export.
Import the settings in Adaptive Temperature, review mappings, then save.
Import preserves calibration data and opens in Analyze only mode.

SETUP

Choose Single tool or Toolchanger (up to four tool profiles). Each tool stores
its filament, nozzle, operating bounds, flow curve, thermal response, burst
preset, and heater/fan mappings. New profiles are unconfigured placeholders;
their default values are not calibration measurements.

Perform hotend calibration before first use, or import a matching saved
calibration. Tool readiness is determined from the response data and its
filament, hotend, nozzle and tool binding. No confirmation checkboxes are used.
The filament name and type default to PLA and can be edited.
Save plugin settings is available at both the top and bottom of Filament curve.

Start with 3-4 tested temperature/flow pairs, or reuse known filament presets
for the same hotend, nozzle and material. The curve supports 2-12 increasing
temperature points. Flow capacity must not decrease as temperature increases.
Use a margin for flow headroom. No temperature extrapolation above the curve
or flow demand above its operating limit is allowed.

Enter measured heating and cooling delays and time constants, or load a
completed response calibration. The predictive planner follows upcoming
path demand using acceleration-aware motion estimates and the learned thermal
response. It anticipates both heating and cooling.

For identical hotend/nozzle/filament combinations, copy calibration between
tools. The destination retains its own tool indices and heater/fan mappings.
Copied data remains marked as inherited and is available immediately for an
identical setup. Tools are calibrated individually; each used tool needs its
own matching calibration data before a toolchanger export can be edited.

Choose an optional hotend geometry preset and adjust Burst scheduling
aggressiveness. A higher setting allows more estimated reserve for short
flow peaks. This reserve is a heuristic, not a measured burst capacity.
Temperature limits, maximum flow, and headroom apply at every setting.
Unlisted hotend uses no burst allowance. Previous hybrid averaging retains
the earlier average-flow policy without a reserve guard.

Settings import/export is available on the Filament curve page. Exported
settings contain the printer details you enter; review them before sharing.
No personal printer configuration or recorded calibration is bundled.

CALIBRATION

Telemetry and event logs use shared UTF-8 writers that flush each record for
live UI readers. Logging failures during cleanup do not prevent heater
shutdown and G-code state restoration. Offline numerical audits cover
extrusion segmentation and fractional timing across 2-100 mm3/s; they do not
establish the physical flow capacity of any hotend.

Live calibration currently supports Moonraker/Klipper from Windows. Other
printer connections require an adapter; offline analysis remains available.
The flow model uses Klipper square corner velocity and minimum cruise ratio.
Other firmware jerk/planner models are not fully simulated.

Enter the printer's IP address and the Moonraker port in separate fields.
The port defaults to 7125 and is editable. Previous saved addresses migrate
to these fields. Check tests the currently entered connection without saving
or requiring completed filament calibration. It only reads server status
and, when available, the selected tool's temperature. Connection failures
and unavailable tool telemetry are reported separately.

Configure the selected tool's extruder heater, part fan,
and tool selection method. Use Already active when your printer requires
manual tool selection, Printer Tn command for compatible toolchanger macros,
or the dedicated Snapmaker U1 selection method when applicable.

Position homes if necessary and moves to the center of reported XY travel
at Z100 mm, accounting for active G-code offsets. Move priority selects Y first,
then X (default), or X first, then Y. Choose the order that clears the printer's
load/dock position. Z clearance is verified before and after tool selection;
each subsequent axis leg completes with a motion wait and coordinate check
before the next begins. Positioning priority saves with the printer settings
and is preserved when changing tools. Reported travel must include
Z100. Keep the bed and tool-change path clear. Loaded measurements extrude
with XYZ stationary and the part fan off. Monitor the extrusion and clear
the collection area between runs.

Flow-load response uses a selected curve point. Heating/cooling response
uses a configurable temperature step under constant flow, within the user's
bounds and the baseline flow capacity. Firmware heater and extrude-only
limits are checked. Printing, paused, and active SD jobs block writes.

Heating/cooling now waits for stability under the actual extrusion load before
advancing each phase. Stability tolerance is editable from 0.05 to 1 percent
of each target in degrees Celsius (default 0.5%; at 230 C this is +/-1.15 C).
Each phase needs a 20-second constant-flow window within that band and a
trend no faster than 0.03 C/s. Minimum loaded phase durations are 30 seconds
at baseline, 35 heating, and 40 cooling. Each times out after 120 seconds;
a timeout aborts the run and turns the heater target off. Use a step of at
least 2 C and at least four times the largest selected tolerance band.
The recorded tolerance is also used by the offline response fitter. Older
recordings keep their original analysis thresholds. Response graphs retain
up to 1,200 samples so longer runs can show both temperature steps.

Tests finish with the selected heater target off. A stop request is
cooperative: response tests use a short motion queue, while a flow-load
batch can take up to 60 seconds to finish.
Keep Orca open until cleanup completes. Measurements characterize the nozzle
sensor response; they do not directly measure melt temperature or print quality.

Run selected test validates and saves the settings currently shown before
starting; a separate save is unnecessary. The response baseline's permitted
flow, including headroom, is shown beside its settings. A reason is displayed
beside Run when the test cannot start. Position also uses the current settings.
Graphs stay compact and resize to fit narrower panels.
The live graph defaults to following the active loaded response phase, with
up to 10 seconds of preceding context. On completion it shows the entire
loaded response, excluding cold warmup and post-flow recovery. Graph view
also offers the entire response or full run; a compact full-run overview
remains visible below. Recorded calibration/cleanup errors appear directly
in the test status, with an exit-code fallback if no event log is available.

EXPORT AND PREVIEW

Select Adaptive temperature output and save output settings before exporting
G-code to a file. The plugin inserts
nonblocking, tool-specific M104 commands after the first layer. Startup,
original commands, and shutdown remain present. Predictions stop at timing
barriers such as unknown macros, heater waits, and tool changes.

Open the processed G-code in Orca's G-code viewer and choose Temperature
to inspect scheduled setpoints. The existing slice Preview does not refresh
from post-processing. Direct upload is blocked in Adaptive temperature mode;
use Analyze only for upload through the current integration.

Logical and physical tool indices must match in this release. Exported nozzle
diameter, filament type and tool mapping are checked against the calibration.
The analysis estimates commanded
flow and sensor response, not actual delivered filament. Pressure advance,
runtime overrides, unknown macros, and firmware differences can affect timing.
Review output and validate the chosen settings on your printer.

The standalone Adaptive Temperature preview.html has no printer connection.
Its controls demonstrate the same UI; settings remain in that page only.

PUBLIC SOURCE AND DEVELOPMENT

Only the current release artifacts are retained. Regression tests use
synthetic profiles and sessions, temporary files, mocked printer commands,
and local test services. They do not operate a real printer. Original printer
backups, private recordings, camera images, obsolete builds and local project
files are not part of the public source tree.

Generated development output belongs in .build, which is excluded by
.gitignore. Run tools/check_public_release.py to scan the public tree and
nested wheels/ZIPs, then tools/build_source_release.py to create a shareable
source archive. See DEVELOPMENT.txt for full instructions.

LICENSES AND SOURCE

Plugin code: GPL-3.0-only. Full terms are in COPYING inside the wheel.
The wheel contains readable Python, HTML, PowerShell, and resource source.
Motion planning portions adapt Klipper code by Kevin O'Connor; see LICENSE.txt.

Hotend reference data: Robert Samples, MeltCalc, CC BY 4.0.
https://meltcalc.baconmilkshake.com/
https://github.com/robertsamples/meltcalc
Snapshot: e6bc61fef79f0da85e84977a878dd74d545b25b3
Geometry columns were normalized and preset identifiers added. Adaptive
Temperature's burst policy is an independent heuristic; no endorsement is
implied. Full data attribution and license are included under resources/meltcalc.
No website code or third-party validation.csv measurements are bundled.

This software and the reference data are provided without warranty.
