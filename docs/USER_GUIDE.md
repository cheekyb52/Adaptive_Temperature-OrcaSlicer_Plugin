# Adaptive Temperature user guide

Version **0.5.8**.

[Project home](../README.md) · [Download the PDF](Adaptive%20Temperature%20Guide/Adaptive%20Temperature%20User%20Guide.pdf?raw=1)

<a name="start"></a>

One filament profile. Temperature that follows the print’s flow demand.

The plugin combines your temperature/flow curve with your hotend’s measured heating and cooling response. It predicts upcoming demand using print speed and acceleration, then adds nozzle temperature commands to exported G-code.

> **Two measurements do different jobs.** The flow curve describes which temperature supports a given flow. The heating/cooling calibration measures how quickly the nozzle sensor responds. You need both for predictive scheduling.

### Install and enable

1. Use an OrcaSlicer 2.5 nightly with **Pages** and **Slicing Pipeline** plugin support and Python 3.12 or newer. This release was checked against nightly `24571737`; menus may vary between nightlies.
2. Install `adaptive_temperature-0.5.8-py3-none-any.whl` through Orca’s plugin manager.
3. Enable the **Adaptive Temperature** Pages capability. Open its tab beside **Project**.
4. In Orca’s **Process** settings, open **Others**. Under **Slicing Pipeline Plugin**, click **+ Add plugin** and select **Adaptive Temperature — slice analysis** for the print. Use Advanced settings mode if the option is hidden.
5. Begin with **Analyze only**. Configure the profile, calibrate, apply the response, then enable temperature output.

### Your first setup

- [01 · Choose tools and identify the filament](#profile)
- [02 · Enter measured flow capacity](#curve)
- [Curve tips · Reuse known presets and compare examples](#curve-tips)
- [03 · Connect and choose a clear positioning path](#connection)
- [04 · Check behavior under flow](#load)
- [05 · Measure heating and cooling](#response)
- [06 · Read the results](#results)
- [07 · Apply and save the response](#apply)
- [08 · Set output and burst behavior](#burst)
- [09 · Export and inspect G-code](#export)
- [10 · Configure multiple tools and back up](#tools)
- [11 · Troubleshooting and input reference](#help)

Pictures show real controls from the released plugin, reframed for readability. Values and results are illustrative, with simulated telemetry. They are not calibration recommendations. Click a picture in the HTML guide to view it at full size. Live calibration currently requires Windows and Moonraker/Klipper; the current live runner and editor support 1.75 mm filament.

<a name="profile"></a>

## Choose the tool and describe its setup

Open **Filament curve**. Select **Single tool** or **Toolchanger** under Printer tools. For a toolchanger, set the tool count and choose the tool you are editing or calibrating.

![Printer tools set to Single tool, followed by Example PLA, hotend description, 0.4 mm nozzle, and physical and logical tool indices 0.](Adaptive%20Temperature%20Guide/images/01-profile.png)

*Tool indices start at zero: T0 is toolhead 1; T3 is toolhead 4.*

| Input | What to enter |
| --- | --- |
| Filament | A recognizable name for this material, such as a brand, product and color. The default is PLA. |
| Filament type in slice | The material type in Orca’s exported slice, such as **PLA**. This is checked during export. |
| Hotend / heater / nozzle | A description that distinguishes the installed setup. Update it when the hardware changes. |
| Calibrated nozzle diameter | The installed nozzle diameter in mm. It must match the exported slice. |
| Physical / sliced tool indices | The printer tool and its G-code tool number. These must match in this release. |

Use **Save plugin settings** at the top or bottom of this page after making changes. A new profile has restrictive placeholder values and an unconfigured response; replace them with your own data.

> Calibration belongs to the filament, hotend, nozzle and tool combination. Changing that identity can invalidate the saved response binding. Load or measure a response for the new setup.

<a name="curve"></a>

## Define temperature versus reliable flow

Enter **3–4 tested temperature/flow pairs** or use known presets for the same setup. [See tips and examples.](#curve-tips) The flow-load test checks thermal behavior; it does not discover flow limits.

![Operating bounds 210 to 240 degrees C, max flow 24, headroom 10 percent; example curve points 210/12, 220/16, 230/20 and 240/24, plus a flow graph and trial input.](Adaptive%20Temperature%20Guide/images/02-curve.png)

*Illustrative curve only. Use your measured temperatures and maximum reliable flows, in mm³/s.*

| Input | Meaning |
| --- | --- |
| Minimum / maximum temperature | The allowed scheduling range. Set the minimum for reliable low-flow print quality and the maximum for your material and hardware. |
| Maximum flow | An absolute configured cap. Keep the flow curve within it. |
| Flow headroom | Reduces the usable curve capacity. With 10% headroom, a measured 24 mm³/s supports up to **21.6 mm³/s**. |
| Flow points | Use 2–12 increasing temperatures; flow capacity must not decrease. The first point must be at the minimum temperature. No extrapolation above the curve. |
| Between measured points | **Linear estimate** interpolates; **Use the higher temperature point** chooses the next hotter measured point. |
| Try a flow | Previews the curve’s temperature estimate, including headroom. It does not operate the printer. |

**Advanced · motion model:** enter the printer’s starting maximum velocity, acceleration, square corner velocity and minimum cruise ratio. G-code speed and acceleration commands update these limits. The planner models acceleration and deceleration; non-Klipper jerk behavior is not fully simulated.

<a name="curve-tips"></a>

## Build on the filament settings you already trust

Start with 3–4 temperature targets and a reliable flow value for each.

Spread the points across the temperature range you intend to print: one at the minimum, one or two intermediate targets, and one at the maximum. This gives the planner a useful scale for choosing temperatures as flow changes.

### Already using several filament presets?

If you have presets that print reliably at different temperatures and maximum volumetric speeds, use their **temperature + flow** pairs as starting data points. Use presets for the same filament, hotend and nozzle combination.

1. Read the normal printing temperature and maximum volumetric speed from each known preset. Flow is in **mm³/s**.
2. Enter those pairs under **Temperature / maximum reliable flow**, ordered from coolest to hottest. Match the first point to your minimum temperature.
3. Keep your chosen flow headroom and use **Try a flow** to check the resulting temperature scale.
4. Complete heating/cooling calibration, apply the response and inspect an exported print before tuning burst aggressiveness.

> An existing preset’s flow limit may be deliberately conservative. It can be a useful starting point without establishing the hotend’s absolute maximum. Extend the curve only with reliable testing of your own setup.

### PLA curve examples

These supplied examples show how different hotends can use different curves. Treat them as setup-specific references and verify suitable values for your own filament, nozzle and hardware.

### Stock Snapmaker U1 hotend

| Temperature | Reliable flow |
| --- | --- |
| 210°C | 15 mm³/s |
| 220°C | 18 mm³/s |
| 230°C | 20 mm³/s |
| 240°C | 24 mm³/s |

### Pika with upgraded 88 W heater

| Temperature | Reliable flow |
| --- | --- |
| 210°C | 30 mm³/s |
| 220°C | 38 mm³/s |
| 230°C | 50 mm³/s |
| 245°C | 75 mm³/s |

With **10% headroom**, these example curves allow up to **21.6 mm³/s** and **67.5 mm³/s**, respectively. Enter the reliable flow values in the curve; the plugin applies headroom separately.

The flow-load test checks thermal tracking at the points you supply. Heating/cooling calibration supplies the predictive timing. Confirm printed quality across the intended range when consolidating several presets into one filament profile.

<a name="connection"></a>

## Connect and position the tool

![Moonraker IP and separate port 7125, extruder and fan mappings, tool selection, and Move priority set to Y first then X.](Adaptive%20Temperature%20Guide/images/03-connection.png)

*192.0.2.10 is a documentation-only IP address. Enter your printer’s actual IP.*

| Input | What to enter or do |
| --- | --- |
| Printer name / IP / Port | A printer label; IP address only, without `http://` or a port; Moonraker port, usually **7125**. Click **Check** to read connection status. |
| Heater / extruder; part-fan object | The exact Klipper object names for the selected tool. `extruder`, `extruder1` and `fan` are examples; verify your printer’s configuration. |
| Tool selection | **Already active** for manual selection; **Printer Tn command / macro** for compatible toolchangers; the dedicated **Snapmaker U1 · Tn A0** option for U1. |
| Move priority | Choose **Y first, then X** or **X first, then Y** to clear your dock or load position. Default Y-first is not a universal clearance guarantee. |

With the bed and tool-change path clear, use **Position … at XY center / Z100 mm**. It homes if needed, verifies Z clearance and tool selection, completes each axis leg separately, then reaches Z100. Reported travel must include Z100.

Position and Run use the settings currently shown; a separate save is unnecessary to enable them. Live tests extrude while XYZ is stationary, with the part fan off. Monitor collection space and clear accumulated extrusion between runs. Printing or paused jobs block calibration commands.

<a name="load"></a>

## Check response under an extrusion load

Select **Flow-load response** and choose a temperature/flow point from your saved curve. Confirm that the selected tool has the intended filament loaded and can sustain that point, then click **Run selected test**.

![Flow-load response selected with the example 220 degree C and 16 mm cubed per second curve point, plus Run selected test and Request stop.](Adaptive%20Temperature%20Guide/images/04-flow-load.png)

*The flow point comes from the curve you entered. This picture uses illustrative values.*

### What the test does

| Stage | Commanded load |
| --- | --- |
| First 5 seconds | 25% of the selected point’s flow. |
| Next 5 seconds | 50% flow. |
| Next 30 seconds | The full selected flow. |
| Final 15 seconds | 25% flow, followed by recovery and heater target off. |

The live graph shows measured nozzle temperature and target; the metrics show commanded flow and heater power. Completed results report the observed temperature range and mean heater power during the full-flow segment.

> **What this establishes:** sensor tracking, thermal sag and recovery at a supplied flow point. It does not measure actual delivered filament, find the failure threshold automatically, or qualify printed strength and appearance.

### After each run

1. Wait until the status completes and cleanup finishes.
2. Inspect the extrusion and temperature behavior. Clear the collection area.
3. Repeat relevant curve points if needed, then run the heating/cooling test to identify the timing values used by the predictor.

**Request stop** is cooperative. A flow-load batch can take up to 60 seconds to finish queued extrusion. Keep Orca open until cleanup completes; tests finish with the heater target off.

<a name="response"></a>

## Measure heating and cooling at constant flow

Choose **Heating / cooling at constant flow**. Use a flow that is supported at the *baseline* temperature after headroom is applied.

![Response test inputs: 12 mm cubed per second, baseline 220 C, step 230 C and stability tolerance 0.5 percent; the allowed baseline flow and test protocol appear below.](Adaptive%20Temperature%20Guide/images/05-response-test.png)

*Example: 12 mm³/s is below this example curve’s 14.4 mm³/s permitted capacity at 220°C.*

| Input | How to choose it |
| --- | --- |
| Response-test flow | A sustainable loaded flow within the displayed baseline limit. Keep this load constant during the temperature steps. |
| Baseline / step temperature | Two temperatures within your bounds. The step must be at least 2°C and at least four times the largest selected tolerance band. |
| Stability tolerance | 0.05–1% of each target in degrees Celsius; default **0.5%**. At 220°C that is ±1.10°C; at 230°C, ±1.15°C. |

Click **Run selected test**. The runner warms up, establishes stable temperature under flow, steps up, then steps back down. Each loaded phase needs 20 seconds inside the tolerance band and drift no faster than 0.03°C/s.

Minimum loaded phases are 30 / 35 / 40 seconds; each can extend to 120 seconds. A timeout fails the run and turns the heater target off. Choose a tolerance that accommodates normal PID variation while retaining a meaningful temperature step. The response test uses a short motion queue for continuous flow.

<a name="results"></a>

## Read the live graph and completed results

![Simulated completed heating and cooling test: solid measured temperature follows dashed target; a full-run overview sits below, and both heating and cooling times to 95 percent appear in Measured results.](Adaptive%20Temperature%20Guide/images/06-results.png)

*Simulated response. The main graph excludes cold warmup; the overview retains the whole run.*

| Display | How to read it |
| --- | --- |
| Solid / dashed lines | Measured nozzle sensor temperature / commanded target. |
| Graph view | **Auto** follows the active phase with preceding context; on completion it shows the loaded response. Choose **Entire calibration response** or **Full run including warmup** for a fixed view. |
| Time to 95% | A summary of each fitted temperature transition. Both heating and cooling need usable fits. |

> **COMPLETE describes the run, not automatic fit approval.** If either direction reports “baseline still drifting” or another identification problem instead of a time, resolve that condition and repeat the response test.

Click **Export results JSON** to retain raw telemetry and analysis. Next, apply the fitted response on Slice analysis; completing the test alone does not apply it to G-code scheduling.

<a name="apply"></a>

## Apply the response to the selected tool

1. Open **Slice analysis** and keep the intended tool selected under Printer tools.
2. Under **Calibrated hotend response**, click **Use latest recorded response**.
3. Review the populated heating and cooling values and response source.
4. Click **Save output settings** to persist the response with this setup.

![Calibrated hotend response with illustrative heating delay 2 seconds, time constant 2.7, cooling delay 3, time constant 3.2; Use latest recorded response and Save output settings buttons.](Adaptive%20Temperature%20Guide/images/07-adopt-response.png)

*Illustrative fitted values. Use your own recorded response rather than the numbers pictured.*

| Value | Meaning |
| --- | --- |
| Delay | The fitted time before the temperature response begins. |
| Time constant | How quickly the response approaches its target after the delay. |
| 95% horizon | Approximately **delay + 3 × time constant**. Heating and cooling have separate horizons. |

**Do not enter the “seconds to 95%” result as the time constant.** The load button transfers the separate fitted terms correctly. **Bind entered values to this setup** is for users who already have appropriate measured delay and time-constant values.

Use the recorded response during the calibration session, then save it and export a settings backup. Saved response settings persist; the latest-recording list is not an automatic cross-version archive of old sessions. The model describes the closed-loop nozzle sensor response under the tested conditions, not direct polymer melt temperature.

<a name="burst"></a>

## Set output mode and burst behavior

![Adaptive temperature M104 output selected, hybrid predictive planner, an unlisted hotend with no burst allowance, and burst aggressiveness at zero.](Adaptive%20Temperature%20Guide/images/08-output-burst.png)

*The saved output notice confirms whether export will insert commands. Save after changing the mode.*

| Setting | Recommended starting approach |
| --- | --- |
| G-code output | Use **Analyze only** while setting up. Select **Adaptive temperature · insert M104 commands** when ready, then click **Save output settings**. |
| Temperature planner | Use **Hybrid average · predictive heating and cooling** for the calibrated response workflow. |
| Hotend geometry preset | Find the matching geometry. Use the unlisted/no-allowance option if it is unknown. A preset does not populate or replace your flow curve or response calibration. |
| Burst aggressiveness | Start at **0** as a conservative baseline. Increase gradually after evaluating print quality. Higher values allow more estimated reserve for brief demand peaks and can permit earlier temperature reductions. |

Keep temperature, flow limits and headroom appropriate for each tool. A small melt zone and a long melt zone may need different slider settings. Zero removes modeled reserve spending; it leaves adaptive temperature scheduling active.

Burst reserve is estimated from hotend geometry, not measured usable melt inventory. The slider is not a temperature percentage. Reference data: Robert Samples, [MeltCalc](https://meltcalc.baconmilkshake.com/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); geometry adapted by Adaptive Temperature. Limits still apply at every slider setting.

<a name="export"></a>

## Verify the processed G-code

1. Under **Process → Others → Slicing Pipeline Plugin**, confirm **Adaptive Temperature — slice analysis** is listed. If needed, click **+ Add plugin** to select it. Slice the plate with the intended printer, nozzle, filament and tool mapping.
2. On Slice analysis, save the desired output mode. Read the **Saved output mode** notice before exporting.
3. **Export G-code to a file.** This runs the plugin’s processing hook.
4. Return to Slice analysis and click **Refresh analysis**. Inspect Latest exported plate and Generated temperature commands.
5. Open that exported file in Orca’s **G-code viewer** and select the **Temperature** color scheme. Inspect the output before printing it.

![Simulated exported plate report with planned and requested flow peaks, adaptive output confirmation, and M104 T0 command examples.](Adaptive%20Temperature%20Guide/images/09-export.png)

*Illustrative export report. An edited export lists tool-specific M104 commands and their setpoint range.*

> **The existing slice Preview does not refresh from post-processing.** To see scheduled setpoints, load the exported G-code. Direct upload is blocked in adaptive output mode; use file export, or Analyze only for the current upload integration.

| Result | What it means |
| --- | --- |
| Requested / planned peak | Requested flow from feed commands / forecast flow after the motion model accounts for acceleration and related limits. |
| Curve estimate at peak | The curve’s temperature for that planned peak; it is not a claim that every move receives that setpoint. |
| M104 commands | Nonblocking temperature targets inserted after the first layer. Existing startup, heater, tool-change and shutdown commands remain present. |

Preview colors show commanded setpoints. Predictive cooling can lower a setpoint while the modeled nozzle is still hot. Forecast average flow is not instantaneous melt flow. Unknown macros, waits and tool changes limit prediction across those boundaries; validate output under your printer’s actual conditions.

<a name="tools"></a>

## Use the right calibration for every tool

In **Toolchanger** mode, configure up to four tools. The Editing / calibrating selection chooses which profile you are changing; it does not restrict export to only that tool.

![Toolchanger with four tools, editing T1, Copy calibration from T0, and Copy to selected tool.](Adaptive%20Temperature%20Guide/images/10-tools.png)

*Copy direction: the source dropdown → the currently selected Editing / calibrating tool.*

### Different hardware or filament

1. Select each tool in turn. Enter its nozzle, filament, flow curve and heater/fan mappings.
2. Calibrate one tool at a time, apply its response and save.
3. Set its geometry preset and burst aggressiveness independently.

During a multicolor or multi-tool export, the plugin schedules every supported used tool after the first layer using its own calibration. If a used tool has missing or mismatched data, export is refused rather than quietly using another tool’s curve.

### Identical setups: copy calibration

1. Finish and save calibration for the source tool.
2. Choose the destination under **Editing / calibrating**.
3. Choose the source under **Copy calibration from**, then click **Copy to selected tool**.
4. Review the destination mapping and save settings.

Copy only between matching hotend, nozzle and filament combinations. The destination retains its own tool indices, heater, fan and selection mapping. Copied data is marked as inherited; it was not independently measured on that tool.

### Back up or upgrade

At the bottom of Filament curve, click **Export settings**. Also keep **Export results JSON** from calibration. Before upgrading, export settings and disable the older pipeline capability so two versions do not process the same file.

Install the new wheel, enable its Pages and pipeline capabilities, **Import settings**, review mappings and save. Import keeps calibration and resets output to **Analyze only**; explicitly select and save adaptive output when ready.

Settings backups contain the printer details you entered. Review them before public sharing. This guide contains only generic examples.

<a name="help"></a>

## When something does not look right

| Symptom | Check or action |
| --- | --- |
| No temperature edits | Read **Saved output mode**. Select adaptive output and save it. Under **Process → Others → Slicing Pipeline Plugin**, use **+ Add plugin** to add Adaptive Temperature — slice analysis if missing. Export a new file, then refresh analysis. |
| Old colors in Preview | Open the newly exported G-code in the G-code viewer and choose Temperature. The original slice preview is unchanged. |
| Run is disabled | Read the reason beside Run. Check IP/port, tool mappings, temperature bounds and baseline flow capacity including headroom. Run applies the shown settings; saving connection first is not required. |
| Check cannot connect | Use an IP-only value and a separate port. Confirm Moonraker is reachable from this PC. A working server connection with missing tool telemetry usually indicates a heater-object mapping problem. |
| Tests cannot run in preview | The standalone preview HTML is deliberately disconnected. Run live calibration inside the installed Orca plugin on Windows. |
| Stability timeout / missing fit | Review the graph and exported results. Check that the flow is sustainable, the targets are separated sufficiently, and tolerance accommodates normal PID variation. Both response directions need fits. |
| Nozzle / material / binding mismatch | Select the matching tool profile or calibrate the actual installed setup. Correct nozzle and material fields; do not relabel another setup’s calibration to bypass the check. |
| Flow exceeds the curve | Reduce requested flow in the print or establish new reliable curve points within the hardware/material limits. Headroom lowers usable capacity. The plugin stops export rather than silently clipping the requirement. |
| Temperatures fall too readily | Reduce burst aggressiveness toward 0 for that tool. Confirm the low-temperature curve points are reliable for the desired print quality and that the correct response was applied. |
| A test fails or needs stopping | Use Request stop and monitor the printer while queued commands finish. Keep Orca open for cleanup. Read the reported error and export results for diagnosis before repeating. |

### Units at a glance

**Flow:** mm³/s, not filament mm/s. **Temperature:** °C. **Delay / time constant:** seconds. **Velocity / corner velocity:** mm/s. **Acceleration:** mm/s². **Stability:** percent of the target in °C. **Burst slider:** 0–100 scheduling aggressiveness, not a percentage temperature change.

The plugin estimates commanded extrusion and nozzle sensor response; it does not directly measure delivered melt. Firmware planner differences, pressure advance, runtime overrides, macros and fan conditions can affect predictions. These instructions describe Adaptive Temperature 0.5.8; use the accompanying README for integration and license details.
