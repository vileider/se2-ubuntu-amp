# Space Engineers 2 dedicated server on Ubuntu with AMP

Run a headless **Space Engineers 2 4.0.0.3169** server with **Y51 SE2 Online 0.18.5** on Ubuntu 24.04 x86-64. CubeCoders AMP manages the Linux process, console commands, player count, world saves and shutdown. Players use the matching Y51 client plugin. This is an unofficial community port.

The original deployment hosted a world in Docker, accepted an in-game LAN connection, saved on shutdown and loaded an imported world with a planet. The server runs with .NET 10 and Pulsar for Linux. LinuxCompat2 and native wrappers still use components from the Windows game runtime. The installation below reconstructs that deployment from upstream downloads on your own host.

## What you need

- Ubuntu 24.04 x86-64, Docker Engine, CubeCoders AMP with Docker instances enabled, Git and Python 3.12.
- A standalone server with the SE2 **4.0.0.3169** runtime from yellow51. An existing extracted 0.18.4-standalone.1 runtime works for this build. For a new download, sign in with Steam and extract 0.18.5-standalone.1 into a private directory. The build script fetches the pinned public Y51 source. [Y51 downloads](https://se2-online.yellow51.dev/#downloads)
- Internet access for the initial build and Steam networking. Game files, credentials and saves stay on the host. Allow enough free space for the extracted standalone, a second instance copy, build files and Docker layers.

## Add mods and plugins from AMP

For an existing instance using the public template, save and stop the game. In ADS choose **Configuration → Instance Deployment → Fetch Latest**, then return to **Instances** and click the download-arrow button labelled **Update Instance** on the **SE2 instance tile**. If ADS asks, confirm **Shutdown and Update**. Wait for the task to finish before reopening the instance. Open the instance and start the game. On first start AMP stages the Python add-on manager and checks its SHA-256. This feature works with the existing Linux image. **Status → Update** inside SE2 continues to update the Y51 game plugin.

If ADS reports `DeploymentTemplates/--main/y51-se2config.json` during **Update Instance**, stop the game and wait for its final save, then run the following on the Ubuntu host. Replace `MySE201` with the existing instance directory name.

```bash
t=/home/amp/.ampdata/instances/ADS01/Plugins/ADSModule/DeploymentTemplates/vileider-se2-ubuntu-amp-main
sudo -u amp git -C "$t" pull --ff-only origin main &&
  sudo -iu amp python3 "$t/repair_instance_template.py" MySE201
```

The helper backs up changed panel files in the reported directory and checks the installed controls and launcher. It restores a running panel after the repair. After `SE2_TEMPLATE_V5_OK`, reopen the instance (start its panel from ADS first if it was offline).

1. Open **Configuration → Dodatki → Mody Steam i pluginy GitHub**. Click **+**, paste a Steam Workshop mod link or item ID, or the GitHub repository URL for a plugin in Pulsar's **PluginHub-SE2**. Save the list. Remove an entry with the list's delete control.
2. With the game running, open **Console**. The manager checks the saved list and reports the title as soon as the source answers, or explains the failure. With the game stopped the check runs at startup. **Znaleziono** confirms the catalog entry. Linux headless compatibility still depends on the plugin. Private items, blueprints, Workshop collections, Windows-only plugins and arbitrary GitHub repositories are outside this manager's supported inputs.
3. For the Verdure 9P server plugin, paste `https://github.com/vileider/se2-verdure-planets` in this same list. The launcher checks the DLL against the packaged SHA-256 on every start. The plugin requests its New Planet dependency from Workshop.

   Restart the game when ready. Y51 downloads Workshop mods and reports world mods through the existing **mods** console command. For GitHub entries, Pulsar uses the selected PluginHub IDs in a copy of the current profile. Its loader reports enabled plugins, initialization counts and errors to the AMP console. A GitHub plugin that adds client UI may also need installing on each player's PC.

The list manages its own selections. Existing manual settings and the original Pulsar profile are preserved. Y51 also loads mods embedded in a world save, so removing a Workshop entry from the panel alone may leave that mod active in an existing world.

## Recover a contract from the AMP console

This optional server-side build targets Y51 0.18.5 with SE2 4.0.0.3169. The installer needs the existing instance, Git and access to the Docker daemon as the AMP service user. It compiles against the game's local assemblies with the pinned SDK. Clients can keep Y51 0.18.5.

Stop the game in AMP and wait for its final save. With the template installed in ADS01, run the following on the Ubuntu host as root. Substitute your AMP instance name for `MySE201`.

```sh
sudo -iu amp bash -c 'cd ~/.ampdata/instances/ADS01/Plugins/ADSModule/DeploymentTemplates/vileider-se2-ubuntu-amp-main && git pull --ff-only origin main && python3 install-contract-admin.py MySE201'
```

After `SE2_CONTRACT_ADMIN_OK`, start the game. Use the server's **Console**. These commands also work for a previously seen offline player whose identity exists in this world.

- `contract list BiKey beacon` lists matching IDs, names, sectors and states. Use `contract list BiKey` to see every contract when the host uses another language.
- `contract complete BiKey <ID>` completes the selected contract and requests a world save. Replace `<ID>` with a listed number. A unique matching contract name also works. For nicknames containing spaces use the player's SteamID64.
- Wait for the confirmation **Zapisano świat po ukończeniu**. Repeating the same command for a completed record leaves the rewards alone.

Completion uses the game's terminal reward operation, including credits, physical items, block unlocks and sector progression as defined by that contract. In faction mode the native reward can reach multiple faction members. Check which contract was previously finished before using this on a rollback. Past payouts missing from the recovered contract history cannot be detected. The command requires a loaded issuing terminal and refuses ambiguous contracts, mixed progress within the group, conflicts with another active group, global contract sharing and repeatable contracts.

The normal **Status → Update** button selects the published stable Y51 DLL. To keep using these opt-in commands after a standard update, rerun the same installer with the game stopped. Reinstalls of the same build use the cached DLL. To switch back explicitly, add `--restore` to the installer command.

## Update the plugin from the AMP panel

With the public template, select **Fetch Latest** in ADS once, then use **Update** on the instance tile to load its new template. For an installation created from the original local template, apply the migration below first. The existing runtime image already includes Python.

When you want to install an update, stop the game in AMP and wait for the final save. Inside that game instance, click **Status → Update**, then **Start** when the update task completes. Look for `SE2_HOSTING_READY` and join with the matching Y51 client. Repeating **Update** on the installed release reports its version and keeps the previous backup.

The update checks the engine and package hashes and replaces `Plugin/Y51_SE2Online.dll`. To roll back, stop the game and restore `Plugin/Y51_SE2Online.dll.previous` under the original DLL name. Match the client version to the restored plugin.

### One-time setup for the original LOCAL/KntSE2 template

In ADS, add `vileider/se2-ubuntu-amp:main` to **Configuration Repositories** and select **Fetch Latest**. On the host, run the following commands for a default ADS01 installation. They copy just the button definition into the template used by the existing instance.

```bash
(
  set -eu
  templates=/home/amp/.ampdata/instances/ADS01/Plugins/ADSModule/DeploymentTemplates
  local_template="$templates/LOCAL-KntSE2-main"
  sudo -u amp install -m 644 "$templates/vileider-se2-ubuntu-amp-main/y51-se2updates.json" "$local_template/y51-se2updates.json"
  sudo -u amp sed -i 's/^App\.UpdateSources=\[\]\r\?$/App.UpdateSources=@IncludeJson[y51-se2updates.json]/' "$local_template/y51-se2.kvp"
  sudo -u amp grep -Fx 'App.UpdateSources=@IncludeJson[y51-se2updates.json]' "$local_template/y51-se2.kvp"
)
```

Then click **Update** on the instance tile in ADS to apply the template. Use **Status → Update** inside the instance for subsequent plugin updates.

### Existing instance still reports that updates are unsupported

If the public template has update stages but the instance's `GenericModule.kvp` still contains `App.UpdateSources=[]`, stop the game from its AMP panel and wait for the final save. Select **Fetch Latest** in ADS to download the helper. For a standard ADS01 installation, run this on the Ubuntu host, replacing `MySE201` with the instance directory name.

```bash
sudo -iu amp python3 /home/amp/.ampdata/instances/ADS01/Plugins/ADSModule/DeploymentTemplates/vileider-se2-ubuntu-amp-main/enable-panel-update.py MySE201
```

The helper backs up `GenericModule.kvp` next to the original. It copies the two update stages into the existing instance while its panel is stopped. If the panel was running, the helper restarts it and checks the stages inside the container. After `SE2_UPDATE_STAGES_OK (container)`, open the game instance and click **Status → Update**. If the check reports `offline instance`, start the instance in ADS first.

### Publishing the next compatible plugin

For a DLL-only update compatible with the same runtime and content, commit the versioned ZIP under `updates/` and update the version and both package hashes in `updates/stable-4.0.0.3169.json` together. ZIPs contain the patched DLL and upstream license. Each click checks this channel. Changes that need new engine binaries, content or dependencies require a separate migration.

### File Manager fallback for 0.18.4

1. Download and unzip [Y51-SE2Online-0.18.5-AMP.zip](updates/Y51-SE2Online-0.18.5-AMP.zip) on your PC. It contains the Linux-patched DLL and the upstream license. The DLL's SHA-256 is `743c11fe1695815ca8619e6430876ebf9d4574aba8eca97b63a9f2447b8ef69b`.
2. Click **Stop** in the game instance and wait for the final save. In **File Manager**, open the existing `SpaceEngineers2/Plugin` folder (or `Plugin` if the file manager opens inside the game directory). Rename `Y51_SE2Online.dll` to `Y51_SE2Online.dll.0.18.4.bak`, then upload the extracted `Y51_SE2Online.dll` into that same folder.
3. Click **Start**. Wait for `SE2_HOSTING_READY`, then connect with a **0.18.5 client** and check the saved world. If startup or joining fails, stop the game, move the new DLL out of the plugin folder, restore the backup's original name, and reconnect with a 0.18.4 client.

The two releases use the same engine build. A comparison of the plugin build outputs found identical Harmony, dependency metadata and all 341 content files. This upgrade works with the existing AMP image, including the streaming-radius variant. For a Windows client installed manually, close the game and extract yellow51's **compiled client 0.18.5** over the current client folder, including its Content directory. [Player downloads and instructions](https://se2-online.yellow51.dev/#downloads)

## Install on the AMP target

Run these commands as the Linux user who owns the standalone files. The example assumes the extracted folder is called `standalone` and contains `runtime/` and `runtime-manifest.json`. The image must be built on the Docker daemon used by the AMP target.

```bash
git clone https://github.com/vileider/se2-ubuntu-amp.git
cd se2-ubuntu-amp
sudo docker build -t local/se2-y51-amp:0.18.4-r2 .
python3 prepare-runtime.py --sudo-docker \
  "$HOME/Downloads/standalone" \
  "$HOME/se2-stage/SpaceEngineers2"
```

Preparation verifies the engine, checks out the pinned Y51 commit, applies the Linux patch and stages a new instance directory. A successful file check prints `SE2_AMP_FILES_OK`. The first in-game connection is the end-to-end test.

1. In the ADS controller, add `vileider/se2-ubuntu-amp:main` to **Configuration Repositories** and select **Fetch Latest**. Create a Linux Docker instance using **KNT / Space Engineers 2 Online (Y51)**. Keep the game stopped until its files are in place.
2. Copy the prepared directory into the new instance. For a standard installation using the system account `amp`, replace `MySE201` below with the instance's actual directory name. The copy requires an empty destination so it cannot overwrite a running world.

   ```bash
   (
     set -eu
     inst=/home/amp/.ampdata/instances/MySE201
     sudo test -f "$inst/AMPConfig.conf"
     stage="$HOME/se2-stage/SpaceEngineers2"
     sudo test -s "$stage/Game2/SpaceEngineers2.dll"
     incoming=$(sudo mktemp -d "$inst/.se2-install.XXXXXXXX")
     sudo cp -a -- "$stage" "$incoming/SpaceEngineers2"
     sudo chown -R --reference="$inst/AMPConfig.conf" "$incoming/SpaceEngineers2"
     sudo mv -T -- "$incoming/SpaceEngineers2" "$inst/SpaceEngineers2"
     sudo rmdir -- "$incoming"
   )
   ```

3. In **Configuration → Serwer**, set the session name, optional password and player limit. **Widocznosc w katalogu Y51** controls public listing. AMP writes that choice into `state/server.json` whenever the game starts. In **Swiat**, choose a world name and the starting scenario for a new world. `EmptyWorld` starts empty.
4. Assign **UDP 27016** for the game and **UDP 27017** for the Steam query. If the game port changes, use the next port for queries. Click **Start** and wait for `SE2_HOSTING_READY` in the console. This means the current process reports a loaded world and a Steam server identity. In the Y51 client choose **Multiplayer → Direct connect** and enter the Ubuntu host address and game port.

## Saves and operation

To save the current world while people are playing, open **Configuration → Świat → Zapisz świat teraz** and click the switch once in either direction. In **Console**, wait for **Zapisano świat** followed by the world name. Y51 emits this acknowledgement after the native save and resume state have been written. A second click requests another save. Wait for the acknowledgement before clicking again. An existing switch position at startup does not request a save. On an older template, the console command `save` already works. **Stop** also requests a final save.

Under **Configuration → Serwer → Promień synchronizacji**, enter a radius in metres (1000–1000000), or `0` for the entire world. A blank field keeps the value in `state/server.json`. Restart the game to apply the change. Template v4 supplies this setting through its bundled panel launcher.

Use **Stop** in AMP for a final save. Look for `Potwierdzony zapis koncowy: True` in the console. Back up both `SpaceEngineers2/profile/` and `SpaceEngineers2/state/` while the game is stopped. The first contains the world and the second contains the resume information and private server configuration. For an existing AMP installation using this port, restore those directories together and keep the same world name and scenario in AMP. The public template starts with directory listing disabled.

## Versions and credits

| Component | Version |
| --- | --- |
| Space Engineers 2 engine | 4.0.0.3169, supplied by the host |
| Y51 SE2 Online | [0.18.5 upstream commit](https://github.com/yellow5111/SE2Online/commit/8f2ccbe713f54ae894496ef34f2975dfa03a301f), patched during local build |
| Pulsar Linux x64 | v2.4.3 |
| CometWorks LinuxCompat2 | `f713880a05e1ffad2d6b489a6c1bc3c36103e030` |
| CometWorks Linux dependencies / native wrappers | v1.0.42 / v1.0.66 |
| Steamworks SDK Redist | SteamCMD application 1007, Linux x64 |

Y51 SE2 Online is provided courtesy of **yellow51 and Vyrex Interactive LLC**. The original license, including its free-distribution, non-commercial and attribution conditions, accompanies the patch. The game and its runtime belong to Keen Software House. Each host obtains its own copy. [Y51 license](LICENSE-Y51.txt)

Pulsar and LinuxCompat2 are fetched from their respective upstream projects. Their licenses and those of their dependencies apply to those components. [Pulsar](https://github.com/SpaceGT/Pulsar) · [LinuxCompat2](https://github.com/CometWorks/linux-compat2) · [Linux dependencies](https://github.com/CometWorks/linux-dependencies) · [Native wrappers](https://github.com/CometWorks/linux-native-wrappers)

For the AMP launcher and template checks, run `python3 -B -m unittest -v test_service` from this repository. Those tests exercise configuration, readiness reporting and save acknowledgements. A multiplayer smoke test requires the game files and a Steam connection.
