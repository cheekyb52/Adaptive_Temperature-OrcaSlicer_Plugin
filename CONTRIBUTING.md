# Contributing to Adaptive Temperature

Use a local Git checkout for changes, and submit focused branches or pull requests. Python regression tests use synthetic profiles and telemetry, local API doubles and a temporary loopback server. They do not operate printer hardware.

## Local setup

Clone the repository with Git, GitHub Desktop or your editor, then open the checkout in your development environment. Use Python 3.12 or newer. Windows PowerShell is required for the live-calibration runner tests.

```text
git clone https://github.com/cheekyb52/Adaptive_Temperature-OrcaSlicer_Plugin.git
cd Adaptive_Temperature-OrcaSlicer_Plugin
```

On the original Windows workspace, `tools\git.cmd` also finds the portable Git copy when Git is not on PATH. Other contributors can use a normal Git installation.

For a downloaded project folder that is not yet connected to Git, run `tools\connect_repo.cmd` from a normal Windows terminal. It connects `origin`, fetches `main`, and initializes tracking without changing working files. It preserves existing commits. Set your preferred Git name and noreply email before committing; the setup script does not choose an identity for contributors.

## Make a change

```text
git switch main
git pull --ff-only
git switch -c improve-flow-planning
```

Edit the source and add regression coverage for behavior changes. Keep printer-specific data out of fixtures. Source remains in its established locations so existing imports and helper scripts keep working:

| Location | Contents |
| --- | --- |
| `plugin/orca_flow/` | Orca capabilities, controller, UI and resources |
| Root Python modules | Motion model, curves, scheduling and fitting |
| Root PowerShell helpers | Calibration, positioning, response and logging |
| `test_*.py`, `verify_*.ps1` | Regression tests and offline fixtures |
| `docs/guide-content.html` | Shared source for the illustrated guide |
| `tools/` | Documentation and public-release tools |
| `dist/` | Current distributable release |

## Check the change

```text
python -B -m unittest discover -s . -p "test_*.py" -v
python tools/build_repo_docs.py --check
python tools/check_public_release.py
git diff --check
```

The test suite builds the current wheel and exercises its packaged capabilities. Windows CI repeats these checks on pushes and pull requests. The offline UI checks can be generated with `python verify_current_ui.py`; open the resulting pages in `.build/ui` and check that their titles begin with `PASS`.

For a UI change, also review the standalone preview and the behavior of relevant controls. Calibration protocol changes need offline control-boundary tests. Live printer testing is a separate, explicit action.

## Update instructions and screenshots

Edit **`docs/guide-content.html`**, then generate GitHub-readable instructions:

```text
python tools/build_repo_docs.py
```

This updates `docs/USER_GUIDE.md`. It preserves the guide's pictures, tables and navigation, so the online instructions have a single editable source.

When the UI or printable instructions change, run the full guide renderer on Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File docs/render_user_guide.ps1
```

The renderer accepts `-Python` and `-Chrome` paths. It uses generic inputs and simulated results. Review the resulting Markdown guide, pictures and PDF, then commit them with the change. Update the README separately when installation, requirements or download links change.

## Prepare a release

1. Update the version in `build_plugin.py`, `plugin/orca_flow/__init__.py`, the UI version labels and guide version labels when changing the release number. Preserve historical G-code marker versions and changelog entries.
2. Add a concise feature/fix entry to `CHANGELOG.txt` and update current-version download links in the README.
3. Build the guide and plugin, then run the checks above. Keep only the current release in `dist/`; earlier releases remain available in Git history.
4. Review `RELEASE_CHECKS.txt` so it describes the current artifact and checks rather than claiming results for an earlier build.
5. Optionally run `python tools/build_source_release.py` for a source ZIP. Source ZIPs are ignored by Git because GitHub already provides source downloads.

The plugin builder writes deterministic ZIP entry timestamps, so an unchanged source tree produces unchanged release artifacts. Document and license changes can still change an artifact's checksum.

## Commit and push

Review the actual diff and staged files before committing. Printer IPs, account paths, credentials, camera images, saved settings and raw telemetry do not belong in the public repository.

```text
git status
git diff
git add <changed source and documentation paths>
git diff --cached
git commit -m "Describe the behavior changed"
git push -u origin improve-flow-planning
```

Open a pull request against `main`. Explain the problem, resulting behavior and relevant validation. Use a GitHub noreply address for commits if you want to keep your personal email out of public history. Authenticate through Git Credential Manager, GitHub Desktop or your preferred GitHub client when required.

## Licensing and attribution

Contributions to the plugin code follow **GPL-3.0-only**. Keep upstream copyright and license notices intact. The bundled MeltCalc data remains separately licensed **CC BY 4.0**; retain its attribution, license and indication of adaptations.
